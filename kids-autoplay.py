#!/usr/bin/env python3
"""
YouTube Kids 자동재생 — Firefox Marionette(내장 자동화)로 DOM을 보고 제어한다.
화면 좌표 클릭(xdotool)과 달리 해상도/레이아웃/언어가 바뀌어도 안 깨진다.

전제: firefox 를 `--marionette` 로 띄워 둔 상태(기본 포트 2828).
동작:
  1) 홈이 로딩되어 썸네일이 뜰 때까지 기다린 뒤, 영상 풀(pool)을 모은다.
  2) 무작위로 하나를 골라 watch 페이지로 이동(=자동재생 + 전체화면).
  3) 영상이 끝나면 다음 영상을 골라 넘긴다. (계속 살아 있는 루프)
     - 재생 중 watch 페이지의 추천 썸네일을 풀에 계속 보충한다.
     - 아이가 일시정지하면 그 영상은 그대로 둔다(끝나야 다음으로 넘어감).
     - 로딩 실패 등으로 재생이 한참 멈춰 있으면 다음 영상으로 건너뛴다.
  4) /tmp/kids-autoplay-cmd 파일을 주기적으로 확인해 skip/pause/resume/fullscreen/
     play:<영상ID> 명령을 처리한다 (kids-control.py 가 기록하는 파일 IPC).
     play:<ID>는 kids-control.py 즐겨찾기 목록에서 특정 영상 재생을 요청할 때 쓰인다.

Firefox 가 닫히면(Marionette 연결 끊김) 조용히 종료한다.
종료코드: 0 정상 종료 / 1 영상을 끝내 못 찾음
"""
import re, socket, json, os, sys, time, random
from collections import deque

HOST, PORT   = "127.0.0.1", 2828
STATUS_FILE  = "/tmp/kids-autoplay-status.json"
CMD_FILE     = "/tmp/kids-autoplay-cmd"
DAILY_FILE   = "/home/jjejje/.kids-daily-watch.json"  # /tmp는 재부팅 시 초기화되므로 홈에 저장


class Marionette:
    """순수 stdlib로 구현한 최소 Marionette 클라이언트 (protocol v3)."""

    def __init__(self, host=HOST, port=PORT, connect_timeout=25, io_timeout=120):
        self.sock = self._connect(host, port, connect_timeout)
        self.sock.settimeout(io_timeout)
        self.buf = b""
        self._id = 0
        self._read_frame()  # 서버가 접속 시 보내는 hello 프레임 소비

    @staticmethod
    def _connect(host, port, deadline_s):
        end = time.time() + deadline_s
        last = None
        while time.time() < end:
            try:
                return socket.create_connection((host, port), timeout=5)
            except OSError as e:
                last = e
                time.sleep(0.5)
        raise IOError("Marionette 포트(%s:%d) 접속 실패: %s" % (host, port, last))

    def _recv_n(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(8192)
            if not chunk:
                raise IOError("Marionette 연결이 닫힘")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read_frame(self):
        length = b""
        while True:
            c = self._recv_n(1)
            if c == b":":
                break
            length += c
        return json.loads(self._recv_n(int(length)).decode("utf-8"))

    def send(self, command, params=None):
        self._id += 1
        payload = json.dumps([0, self._id, command, params or {}]).encode("utf-8")
        self.sock.sendall(b"%d:%s" % (len(payload), payload))
        resp = self._read_frame()
        if not isinstance(resp, list) or len(resp) < 4:
            raise IOError("예상치 못한 응답: %r" % (resp,))
        err, result = resp[2], resp[3]
        if err is not None:
            raise RuntimeError("Marionette 오류(%s): %s" % (command, err))
        return result

    def new_session(self):
        return self.send("WebDriver:NewSession", {})

    def execute_script(self, script, args=None):
        r = self.send("WebDriver:ExecuteScript",
                      {"script": script, "args": args or [], "newSandbox": False})
        return r.get("value") if isinstance(r, dict) else r

    def navigate(self, url):
        return self.send("WebDriver:Navigate", {"url": url})


# 섀도우 DOM까지 뚫고 들어가 보이는 watch 링크들을 수집한다.
FIND_THUMBS_JS = r"""
const seen = new Set();
const out = [];
function walk(root) {
  let anchors;
  try { anchors = root.querySelectorAll('a[href]'); } catch (e) { return; }
  for (const a of anchors) {
    const href = a.href || '';
    if (href.indexOf('/watch?v=') === -1) continue;
    if (seen.has(href)) continue;
    const r = a.getBoundingClientRect();
    if (r.width > 60 && r.height > 40) { seen.add(href); out.push(href); }
  }
  let kids;
  try { kids = root.querySelectorAll('*'); } catch (e) { return; }
  for (const el of kids) { if (el.shadowRoot) walk(el.shadowRoot); }
}
walk(document);
return out;
"""

# watch 페이지에서 <video> 의 상태를 본다.
VIDEO_STATE_JS = r"""
const nudge = arguments[0];
function findVideo(root) {
  const v = root.querySelector('video');
  if (v) return v;
  for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) { const r = findVideo(el.shadowRoot); if (r) return r; }
  }
  return null;
}
const v = findVideo(document);
if (!v) return {ok: 0};
if (nudge && v.paused && !v.ended) { try { v.play(); } catch (e) {} }
return {
  ok: 1,
  ended: !!v.ended,
  ct: v.currentTime || 0,
  dur: (isFinite(v.duration) ? v.duration : 0),
  paused: !!v.paused
};
"""

# YouTube 플레이어를 전체화면으로 전환한다.
FULLSCREEN_JS = r"""
function deep(sel) {
  function walk(root) {
    let el; try { el = root.querySelector(sel); } catch (e) { return null; }
    if (el) return el;
    let kids; try { kids = root.querySelectorAll('*'); } catch (e) { return null; }
    for (const e of kids) { if (e.shadowRoot) { const r = walk(e.shadowRoot); if (r) return r; } }
    return null;
  }
  return walk(document);
}
if (document.fullscreenElement) return 'already';
const btn = deep('.ytp-fullscreen-button');
if (btn) { try { btn.click(); return 'button'; } catch (e) {} }
function fv(root) {
  const v = root.querySelector('video'); if (v) return v;
  for (const e of root.querySelectorAll('*')) { if (e.shadowRoot) { const r = fv(e.shadowRoot); if (r) return r; } }
  return null;
}
const v = fv(document);
if (v && v.requestFullscreen) { try { v.requestFullscreen(); return 'video'; } catch (e) { return 'err'; } }
return 'none';
"""

# 현재 재생 중인 영상 제목 (탭 타이틀에서 " - YouTube Kids" 접미사 제거).
TITLE_JS = r"""
let t = document.title || '';
t = t.replace(/\s*-\s*YouTube Kids\s*$/i, '');
return t;
"""

PAUSE_JS = r"""
function fv(r){const v=r.querySelector('video');if(v)return v;
  for(const e of r.querySelectorAll('*')){if(e.shadowRoot){const x=fv(e.shadowRoot);if(x)return x;}}return null;}
const v=fv(document); if(v&&!v.paused){try{v.pause();}catch(e){}} return v?1:0;
"""

RESUME_JS = r"""
function fv(r){const v=r.querySelector('video');if(v)return v;
  for(const e of r.querySelectorAll('*')){if(e.shadowRoot){const x=fv(e.shadowRoot);if(x)return x;}}return null;}
const v=fv(document); if(v&&v.paused){try{v.play();}catch(e){}} return v?1:0;
"""


# ── 일일 누적 시청 시간 (자정 리셋, 일시정지 제외) ────────────────────────────
# kids-timer-bar.py(세션 UI)와 무관하게, 실제 재생 감시 루프를 도는 이 프로세스가
# 살아있는 동안 계속 기록한다 — 세션 시간 조정/타이머바 재시작에 영향받지 않는다.

_daily         = None
_daily_last_ts = None


def _today_str():
    return time.strftime("%Y-%m-%d")


def _load_daily():
    try:
        with open(DAILY_FILE) as f:
            d = json.load(f)
        if d.get("date") == _today_str():
            return d
    except Exception:
        pass
    return {"date": _today_str(), "seconds": 0}


def _save_daily(d):
    try:
        with open(DAILY_FILE, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def _tick_daily(is_paused):
    """호출 시점 기준 경과 시간을 오늘 누적 시청시간에 더한다."""
    global _daily, _daily_last_ts
    now = time.time()
    if _daily is None:
        _daily = _load_daily()
        _daily_last_ts = now
        return
    dt = min(max(0.0, now - _daily_last_ts), 5.0)  # 순간 정지/드리프트 대비 상한
    _daily_last_ts = now
    if _daily.get("date") != _today_str():
        _daily = {"date": _today_str(), "seconds": 0}
    if not is_paused:
        _daily["seconds"] += dt
    _save_daily(_daily)


# ── 상태 파일 / 명령 파일 IPC ────────────────────────────────────────────────

def _write_status(url, st, pool_size, title=""):
    """kids-control.py 가 읽는 상태 파일을 갱신한다."""
    try:
        obj = {
            "url":       url or "",
            "title":     title or "",
            "paused":    st.get("paused"),
            "ct":        st.get("ct", 0),
            "dur":       st.get("dur", 0),
            "pool_size": pool_size,
            "state":     ("idle"   if not st.get("ok")
                          else ("paused" if st.get("paused") else "playing")),
            "ts":        time.time(),
        }
        with open(STATUS_FILE, "w") as f:
            json.dump(obj, f)
    except Exception:
        pass


_VIDEO_ID_RE = re.compile(r"^[\w-]{5,40}$")


def _check_cmd(m):
    """명령 파일을 읽고 실행한다.
    반환: '' (계속 진행) / 'skip' (다음 영상으로) / 'play:<ID>' (지정 영상 재생, kids-control.py 즐겨찾기 목록에서 요청)"""
    try:
        if not os.path.exists(CMD_FILE):
            return ""
        with open(CMD_FILE) as f:
            cmd = f.read().strip()
        os.unlink(CMD_FILE)
    except Exception:
        return ""

    if cmd == "skip":
        return "skip"
    if cmd.startswith("play:"):
        vid = cmd.split(":", 1)[1].strip()
        return cmd if _VIDEO_ID_RE.match(vid) else ""
    try:
        if cmd == "pause":
            m.execute_script(PAUSE_JS)
        elif cmd == "resume":
            m.execute_script(RESUME_JS)
        elif cmd == "fullscreen":
            m.execute_script(FULLSCREEN_JS)
    except IOError:
        raise
    except Exception:
        pass
    return ""


# ── Marionette 유틸 ──────────────────────────────────────────────────────────

def collect_thumbs(m):
    try:
        return m.execute_script(FIND_THUMBS_JS) or []
    except IOError:
        raise
    except Exception:
        return []


def video_state(m, nudge=False):
    try:
        return m.execute_script(VIDEO_STATE_JS, [nudge]) or {}
    except IOError:
        raise
    except Exception:
        return {}


def get_title(m):
    try:
        return m.execute_script(TITLE_JS) or ""
    except IOError:
        raise
    except Exception:
        return ""


def go_fullscreen(m):
    """navigate 후 매번 호출해 전체화면 진입을 확인한다."""
    for _ in range(5):
        try:
            r = m.execute_script(FULLSCREEN_JS)
        except IOError:
            raise
        except Exception:
            r = None
        if r in ("already", "button", "video"):
            time.sleep(0.6)
            try:
                if m.execute_script("return !!document.fullscreenElement;"):
                    return True
            except IOError:
                raise
            except Exception:
                pass
        time.sleep(0.6)
    return False


def pick(pool, recent):
    candidates = [h for h in pool if h not in recent] or pool
    return random.choice(candidates)


def start_playing(m, url):
    """watch 페이지로 이동하고 재생이 시작될 때까지 떠민 뒤 전체화면으로."""
    m.navigate(url)
    playing = False
    for _ in range(8):
        st = video_state(m, nudge=True)
        _tick_daily(bool(st.get("paused")))
        if st.get("ok") and not st.get("paused"):
            playing = True
            break
        time.sleep(1)
    go_fullscreen(m)
    return playing


def monitor_until_done(m, url, pool, title="", max_stall=40):
    """현재 영상이 끝날 때까지 감시. 2초마다 상태 기록 + 명령 처리.
    반환: 'ended' / 'stall' / 'novideo' / 'skip' / 'play:<ID>'"""
    last_ct      = -1.0
    stall_since  = time.time()
    novideo_since = None
    while True:
        time.sleep(2)
        cmd_result = _check_cmd(m)
        if cmd_result:
            return cmd_result
        st = video_state(m)
        _write_status(url, st, len(pool), title)
        _tick_daily(bool(st.get("paused")))
        if not st.get("ok"):
            novideo_since = novideo_since or time.time()
            if time.time() - novideo_since > 20:
                return "novideo"
            continue
        novideo_since = None
        if st.get("ended"):
            return "ended"
        dur, ct = st.get("dur", 0), st.get("ct", 0)
        if dur and ct >= dur - 1.0:
            return "ended"
        if st.get("paused"):
            stall_since = time.time()
            last_ct = ct
            continue
        if abs(ct - last_ct) > 0.3:
            last_ct = ct
            stall_since = time.time()
        elif time.time() - stall_since > max_stall:
            return "stall"


# ── 메인 루프 ────────────────────────────────────────────────────────────────

def main():
    try:
        m = Marionette()
        m.new_session()
    except Exception as e:
        print("autoplay: Marionette 접속 실패:", e, file=sys.stderr)
        return 1

    deadline = time.time() + 45
    pool = []
    while time.time() < deadline:
        try:
            pool = collect_thumbs(m)
        except IOError:
            print("autoplay: 연결 종료(시작 전)", file=sys.stderr)
            return 0
        if len(pool) >= 3:
            break
        time.sleep(1.0)

    if not pool:
        print("autoplay: 재생할 영상을 찾지 못함", file=sys.stderr)
        return 1

    recent = deque(maxlen=15)
    next_target = None  # play:<ID> 명령으로 지정된 다음 영상 (없으면 무작위 선택)
    print("autoplay: 시작 — 풀 %d개" % len(pool))

    try:
        while True:
            if next_target:
                target = next_target
                next_target = None
            else:
                target = pick(pool, recent)
            recent.append(target)
            _write_status(target, {"ok": 0}, len(pool))
            print("autoplay: ▶ %s (풀 %d)" % (target, len(pool)))
            start_playing(m, target)
            title = get_title(m)

            result = monitor_until_done(m, target, pool, title)
            print("autoplay: ◼ %s → %s" % (target, result))

            if result.startswith("play:"):
                next_target = "https://www.youtubekids.com/watch?v=" + result.split(":", 1)[1].strip()

            more = collect_thumbs(m)
            if more:
                have = set(pool)
                pool.extend(h for h in more if h not in have)
                if len(pool) > 120:
                    pool = pool[-120:]
    except IOError:
        print("autoplay: Firefox 종료 감지 — 자동재생 루프 종료", file=sys.stderr)
        _write_status("", {}, 0)
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
