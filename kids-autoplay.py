#!/usr/bin/env python3
"""
YouTube Kids 자동재생 — Firefox Marionette(내장 자동화)로 DOM을 보고 제어한다.
화면 좌표 클릭(xdotool)과 달리 해상도/레이아웃/언어가 바뀌어도 안 깨진다.

전제: firefox 를 `--marionette` 로 띄워 둔 상태(기본 포트 2828).
동작:
  1) 홈이 로딩되어 썸네일이 뜰 때까지 기다린 뒤, 영상 풀(pool)을 모은다.
  2) 무작위로 하나를 골라 watch 페이지로 이동(=자동재생)한다.
  3) 영상이 끝나면 다음 영상을 골라 넘긴다. (계속 살아 있는 루프)
     - 재생 중 watch 페이지의 추천 썸네일을 풀에 계속 보충한다.
     - 아이가 일시정지하면 그 영상은 그대로 둔다(끝나야 다음으로 넘어감).
     - 로딩 실패 등으로 재생이 한참 멈춰 있으면 다음 영상으로 건너뛴다.

Firefox 가 닫히면(Marionette 연결 끊김) 조용히 종료한다.
종료코드: 0 정상 종료 / 1 영상을 끝내 못 찾음
"""
import socket, json, sys, time, random
from collections import deque

HOST, PORT = "127.0.0.1", 2828


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
            except OSError as e:  # 포트가 아직 안 열렸을 수 있음 → 재시도
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
        # 프레이밍: "<바이트길이>:<json>"
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
        resp = self._read_frame()  # [1, id, error, result]
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

# YouTube 플레이어를 전체화면으로 전환(옆 추천 썸네일을 가린다).
# 'f' 키와 동일한 효과인 풀스크린 버튼을 우선 클릭하고, 없으면 video 자체를 풀스크린.
# (user.js의 full-screen-api.allow-trusted-requests-only=false 로 제스처 없이 허용)
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

# watch 페이지에서 <video> 의 상태를 본다(+막혀 있으면 play() 한번 시도).
# nudge=true 일 때만 재생을 떠밀어, 아이가 일시정지한 영상은 다시 안 튼다.
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


def collect_thumbs(m):
    try:
        return m.execute_script(FIND_THUMBS_JS) or []
    except IOError:
        raise
    except Exception:
        return []


def video_state(m, nudge=False):
    """영상 상태 dict. 페이지 전환 중 일시적 스크립트 오류는 {} 로 흡수.
    연결이 끊기면(IOError) 그대로 올려 보내 루프를 종료시킨다."""
    try:
        return m.execute_script(VIDEO_STATE_JS, [nudge]) or {}
    except IOError:
        raise
    except Exception:
        return {}


def pick(pool, recent):
    """최근 본 것은 피해서 무작위로 하나 고른다."""
    candidates = [h for h in pool if h not in recent] or pool
    return random.choice(candidates)


def go_fullscreen(m):
    """YouTube 플레이어를 전체화면으로 — 옆 추천 썸네일을 가린다.
    navigate 할 때마다 풀스크린이 풀리므로 영상마다 다시 호출한다."""
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


def start_playing(m, url):
    """watch 페이지로 이동하고 실제 재생이 시작될 때까지 몇 번 떠민 뒤, 전체화면으로."""
    m.navigate(url)
    playing = False
    for _ in range(8):
        st = video_state(m, nudge=True)
        if st.get("ok") and not st.get("paused"):
            playing = True
            break
        time.sleep(1)
    go_fullscreen(m)
    return playing


def monitor_until_done(m, max_stall=40):
    """현재 영상이 끝날 때까지 감시. 반환:
       'ended'  정상 종료(또는 거의 끝까지 봄)
       'stall'  재생이 멈춘 채 너무 오래(로딩 실패 추정) → 건너뜀
       'novideo' 영상 요소를 한참 못 찾음 → 건너뜀
    """
    last_ct = -1.0
    stall_since = time.time()
    novideo_since = None
    while True:
        time.sleep(2)
        st = video_state(m)  # 감시 중엔 떠밀지 않음(아이의 일시정지 존중)
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

        # 일시정지면 아이의 선택 → 계속 기다림(스톨 타이머 리셋)
        if st.get("paused"):
            stall_since = time.time()
            last_ct = ct
            continue
        # 재생 중인데 시간이 안 흐르면(버퍼링/에러) 스톨 카운트
        if abs(ct - last_ct) > 0.3:
            last_ct = ct
            stall_since = time.time()
        elif time.time() - stall_since > max_stall:
            return "stall"


def main():
    try:
        m = Marionette()
        m.new_session()
    except Exception as e:
        print("autoplay: Marionette 접속 실패:", e, file=sys.stderr)
        return 1

    # 홈에 썸네일이 충분히 뜰 때까지 최대 ~45초 대기
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
    print("autoplay: 시작 — 풀 %d개" % len(pool))

    try:
        while True:
            target = pick(pool, recent)
            recent.append(target)
            print("autoplay: ▶ %s (풀 %d)" % (target, len(pool)))
            start_playing(m, target)

            result = monitor_until_done(m)
            print("autoplay: ◼ %s → %s" % (target, result))

            # watch 페이지의 추천 썸네일을 풀에 보충(중복 제외)
            more = collect_thumbs(m)
            if more:
                have = set(pool)
                pool.extend(h for h in more if h not in have)
                # 풀이 너무 커지지 않게 상한
                if len(pool) > 120:
                    pool = pool[-120:]
    except IOError:
        print("autoplay: Firefox 종료 감지 — 자동재생 루프 종료", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
