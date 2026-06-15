#!/usr/bin/env python3
"""
kids-control.py — SSH 터미널에서 YouTube Kids 키오스크를 모니터링/조작

사용법: python3 /home/jjejje/kids-machine/kids-control.py
종료:  q  (키오스크에는 영향 없음)

제어 명령은 /tmp/kids-autoplay-cmd 파일을 통해 kids-autoplay.py 로 전달된다.
kids-autoplay.py 가 살아 있을 때만 skip/pause/resume/fullscreen 이 동작한다.
"""
import curses, fcntl, json, locale, os, re, signal, socket, subprocess, sys, time, unicodedata
from datetime import datetime

STATE_FILE       = "/tmp/kids-kiosk-state.json"
STATUS_FILE      = "/tmp/kids-autoplay-status.json"
CMD_FILE         = "/tmp/kids-autoplay-cmd"
AUTOPLAY_PIDFILE = "/tmp/kids-autoplay.pid"
TIMER_PIDFILE    = "/tmp/kids-kiosk-timer.pid"
TIMERBAR_PIDFILE = "/tmp/kids-timerbar.pid"
GRAB_PIDFILE     = "/tmp/kids-kb-grabber.pid"
GRAB_STATEFILE   = "/tmp/kids-grabber-state.json"
TIMERBAR_SCRIPT  = "/home/jjejje/kids-machine/kids-timer-bar.py"
KIOSK_SCRIPT     = "/home/jjejje/kids-machine/kids-kiosk.sh"
LOCK_FILE        = "/tmp/kids-control.lock"
TMUX_SESSION     = "kids-control"

REFRESH = 2  # 자동 갱신 주기(초)

_lock_fh = None  # 단일 인스턴스 잠금 파일 핸들 (GC 방지용)


def _dw(s):
    """문자열의 터미널 표시 너비 — 한글 등 전각문자는 2칸으로 계산."""
    return sum(
        2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        for c in s
    )


def _acquire_lock():
    """배타적 잠금 획득. 이미 실행 중이면 False 반환.
    flock 은 프로세스 종료(정상/비정상 모두)시 OS 가 자동 해제한다."""
    global _lock_fh
    _lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _lock_fh.close()
        _lock_fh = None
        return False
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()
    return True


# ── 상태 수집 ────────────────────────────────────────────────────────────────

def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _pid_alive(path):
    try:
        pid = int(open(path).read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def _ff_pid():
    """kids-kiosk 프로필로 실행 중인 Firefox 의 PID"""
    r = subprocess.run(["pgrep", "-f", "mozilla/kids-kiosk"],
                       capture_output=True, text=True)
    pids = [p for p in r.stdout.strip().split() if p]
    return int(pids[0]) if pids else None


def _endscreen_pid():
    """kids-end-screen.py 프로세스 PID (없으면 None)"""
    r = subprocess.run(["pgrep", "-f", "kids-end-screen"],
                       capture_output=True, text=True)
    pids = [p for p in r.stdout.strip().split() if p]
    return int(pids[0]) if pids else None


def _marionette_up():
    try:
        s = socket.create_connection(("127.0.0.1", 2828), timeout=0.5)
        s.close()
        return True
    except Exception:
        return False


def gather():
    state  = _read_json(STATE_FILE)
    status = _read_json(STATUS_FILE)
    gstate = _read_json(GRAB_STATEFILE)
    ff_pid = _ff_pid()
    ap_pid = _pid_alive(AUTOPLAY_PIDFILE)
    es_pid = _endscreen_pid()
    gr_pid = _pid_alive(GRAB_PIDFILE)

    remaining = None
    if state.get("end_ts"):
        remaining = max(0.0, state["end_ts"] - time.time())

    url    = status.get("url", "")
    vid_id = (url.split("watch?v=")[-1].split("&")[0]
              if "watch?v=" in url else "")
    age    = (time.time() - status["ts"]) if status.get("ts") else None

    return dict(
        running   = ff_pid is not None,
        ff_pid    = ff_pid,
        ap_pid    = ap_pid,
        endscreen = es_pid is not None,
        es_pid    = es_pid,
        grab_pid  = gr_pid,
        kb_locked    = gstate.get("kb_locked"),    # None = 정보 없음
        mouse_locked = gstate.get("mouse_locked"),
        marionette= _marionette_up(),
        autoplay  = state.get("autoplay", False),
        minutes   = state.get("minutes"),
        remaining = remaining,
        url       = url,
        vid_id    = vid_id,
        paused    = status.get("paused"),   # None = 정보 없음
        ct        = status.get("ct", 0),
        dur       = status.get("dur", 0),
        pool      = status.get("pool_size", 0),
        age       = age,
    )


# ── 제어 ────────────────────────────────────────────────────────────────────

def send_cmd(cmd):
    try:
        with open(CMD_FILE, "w") as f:
            f.write(cmd)
        return True
    except Exception:
        return False


# ── 음량 ─────────────────────────────────────────────────────────────────────

def _audio_env():
    """pactl 이 사용자 PulseAudio/PipeWire 소켓에 접근하도록 환경을 보강한다.
    SSH 세션에 XDG_RUNTIME_DIR 이 없을 수 있어 /run/user/<uid> 로 보완."""
    env = {**os.environ}
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _pactl(args):
    try:
        return subprocess.run(["pactl", *args], env=_audio_env(),
                              capture_output=True, text=True, check=False)
    except Exception:
        return None


def adjust_volume(delta):
    """기본 싱크 음량을 delta(예: '+5%', '-5%') 만큼 조정하고 결과 메시지 반환."""
    _pactl(["set-sink-volume", "@DEFAULT_SINK@", delta])
    v = get_volume()
    if v is None:
        return "→ 음량 조정"
    return f"→ 음량 {v}%"


def toggle_mute():
    _pactl(["set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    r = _pactl(["get-sink-mute", "@DEFAULT_SINK@"])
    if r and "yes" in r.stdout:
        return "→ 음소거 ON"
    if r and "no" in r.stdout:
        return "→ 음소거 OFF"
    return "→ 음소거 토글"


def get_volume():
    """기본 싱크 음량(%) 정수 또는 None."""
    r = _pactl(["get-sink-volume", "@DEFAULT_SINK@"])
    if not r:
        return None
    m = re.search(r"(\d+)%", r.stdout)
    return int(m.group(1)) if m else None


def kill_kiosk():
    subprocess.run(["pkill", "-f", "youtubekids.com"], check=False)


def dismiss_endscreen():
    """종료화면을 닫는다 — SIGTERM → _exit() → 키보드 그랩 해제 후 종료."""
    pid = _endscreen_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def toggle_lock(grab_pid, sig):
    """kb-grabber 에 시그널을 보내 잠금을 토글한다.
    SIGUSR1 = 키보드, SIGUSR2 = 마우스 (kids-kb-grabber.py 와 약속)"""
    try:
        os.kill(grab_pid, sig)
        return True
    except Exception:
        return False


def extend_session(scr):
    """종료화면을 닫고 새 세션을 시작한다.
    입력 폼을 먼저 받은 뒤 종료화면을 닫아 아이가 화면이 닫히는 것을 최소한으로 본다."""
    params = ask_start_params(scr)
    if not params:
        return None
    minutes, autoplay, sat = params
    dismiss_endscreen()
    time.sleep(1.5)  # kids-kiosk.sh 정리 완료 대기
    start_kiosk(minutes, autoplay, sat)
    return params


def _x11_env():
    """실행 중인 X 세션의 DISPLAY/XAUTHORITY 환경변수를 추출한다."""
    env = {"DISPLAY": ":0"}
    for name in ["xfce4-session", "xfwm4", "Xorg"]:
        r = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
        pid = r.stdout.strip().split("\n")[0].strip()
        if not pid:
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                for item in f.read().split(b"\0"):
                    s = item.decode("utf-8", errors="ignore")
                    if s.startswith("XAUTHORITY="):
                        env["XAUTHORITY"] = s.split("=", 1)[1]
                        return env
        except Exception:
            continue
    return env


def start_kiosk(minutes, autoplay, saturation):
    """키오스크를 SSH 세션과 독립된 프로세스로 시작한다."""
    env = {**os.environ, **_x11_env()}
    subprocess.Popen(
        ["bash", KIOSK_SCRIPT,
         str(minutes), "True" if autoplay else "False", str(saturation)],
        env=env, close_fds=True, start_new_session=True,
        stdout=open("/tmp/kids-kiosk-start.log", "w"),
        stderr=subprocess.STDOUT,
    )


def _readline(scr, row, col, maxlen=6, default=""):
    """curses 한 줄 입력. Enter→완료, ESC→None."""
    buf = list(default)
    scr.addstr(row, col, default.ljust(maxlen, "░"))
    scr.move(row, col + len(buf))
    scr.refresh()
    scr.nodelay(False)
    while True:
        ch = scr.getch()
        if ch == 27:
            scr.nodelay(True)
            return None
        elif ch in (10, 13):
            scr.nodelay(True)
            return "".join(buf) if buf else default
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if buf:
                buf.pop()
                pos = col + len(buf)
                scr.addstr(row, pos, "░" * (maxlen - len(buf)))
                scr.move(row, pos)
        elif 32 <= ch < 127 and len(buf) < maxlen:
            buf.append(chr(ch))
            scr.addch(row, col + len(buf) - 1, chr(ch))
            scr.addstr(row, col + len(buf), "░" * (maxlen - len(buf)))
            scr.move(row, col + len(buf))
        scr.refresh()


def ask_start_params(scr):
    """키오스크 시작 파라미터를 curses 화면에서 입력받는다.
    반환: (minutes, autoplay, saturation) 또는 None(취소)"""
    scr.erase()
    h, w = scr.getmaxyx()
    C1 = curses.color_pair(1)
    C3 = curses.color_pair(3)

    scr.addstr(0, 0, "─" * (w - 1), C1)
    title = "  키오스크 시작 설정  "
    scr.addstr(0, max(0, (w - _dw(title)) // 2), title, C1 | curses.A_BOLD)
    scr.addstr(2, 2, "ESC: 취소   Enter: 확인", curses.A_DIM)

    def ask_int(row, prompt, default, lo, hi):
        scr.addstr(row, 2, prompt)
        scr.refresh()
        curses.curs_set(1)
        raw = _readline(scr, row, 2 + _dw(prompt), maxlen=4, default=str(default))
        curses.curs_set(0)
        if raw is None:
            return None
        try:
            v = int(raw)
            return max(lo, min(hi, v))
        except ValueError:
            return default

    # 시청 시간
    minutes = ask_int(4, "시청 시간 (분, 1-300) : ", 30, 1, 300)
    if minutes is None:
        return None

    # 자동재생
    _ap_prompt = "자동재생? [Y/n] : "
    scr.addstr(6, 2, _ap_prompt)
    scr.refresh()
    autoplay = True
    while True:
        ch = scr.getch()
        if ch == 27:
            return None
        elif ch in (ord("n"), ord("N")):
            autoplay = False
            scr.addstr(6, 2 + _dw(_ap_prompt), "n")
            break
        elif ch in (ord("y"), ord("Y"), 10, 13):
            scr.addstr(6, 2 + _dw(_ap_prompt), "y")
            break
        scr.refresh()

    # 채도
    saturation = ask_int(8, "채도 (0-100, 기본 100) : ", 100, 0, 100)
    if saturation is None:
        return None

    # 최종 확인
    scr.addstr(10, 2,
               f"▶ {minutes}분, 자동재생={'ON' if autoplay else 'OFF'}, 채도={saturation}%",
               C3 | curses.A_BOLD)
    scr.addstr(11, 2, "시작: Enter   취소: ESC")
    scr.refresh()
    while True:
        ch = scr.getch()
        if ch == 27:
            return None
        elif ch in (10, 13):
            return (minutes, autoplay, saturation)


def adjust_time(delta_minutes):
    """남은 세션 시간을 delta_minutes 만큼 늘리거나 줄인다."""
    state = _read_json(STATE_FILE)
    if not state:
        return "세션 정보 없음"

    now     = time.time()
    old_end = state.get("end_ts", now)
    new_end = max(now + 60, old_end + delta_minutes * 60)  # 최소 1분 보장

    # 상태 파일 업데이트
    state["end_ts"] = new_end
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        return "상태 파일 쓰기 실패"

    # 기존 타이머 프로세스 종료
    old_timer = _pid_alive(TIMER_PIDFILE)
    if old_timer:
        try:
            os.kill(old_timer, signal.SIGTERM)
        except Exception:
            pass

    # 새 타이머 시작 (bash 서브쉘: sleep → flag → pkill)
    new_secs = max(1, int(new_end - time.time()))
    proc = subprocess.Popen(
        ["bash", "-c",
         f"sleep {new_secs} && touch /tmp/kids-timer-ended "
         f"&& pkill -f youtubekids.com"],
        close_fds=True, start_new_session=True,
    )
    try:
        with open(TIMER_PIDFILE, "w") as f:
            f.write(str(proc.pid))
    except Exception:
        pass

    # 타이머 바 재시작 (DISPLAY=:0 로 X11 접근)
    old_bar = _pid_alive(TIMERBAR_PIDFILE)
    if old_bar:
        try:
            os.kill(old_bar, signal.SIGTERM)
        except Exception:
            pass
    bar_env = {**os.environ, "DISPLAY": ":0"}
    bar_proc = subprocess.Popen(
        ["python3", TIMERBAR_SCRIPT, "--end-ts", str(new_end)],
        env=bar_env, close_fds=True, start_new_session=True,
    )
    try:
        with open(TIMERBAR_PIDFILE, "w") as f:
            f.write(str(bar_proc.pid))
    except Exception:
        pass

    sign     = "+" if delta_minutes > 0 else ""
    new_mins = int((new_end - time.time()) / 60)
    return f"→ {sign}{delta_minutes}분 조정 — 남은 시간 약 {new_mins}분"


# ── 포맷 ────────────────────────────────────────────────────────────────────

def fmt_dur(secs):
    if not secs:
        return "--:--"
    h, rem = divmod(int(secs), 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def pbar(ct, dur, width=28):
    if not dur:
        return "░" * width
    filled = int(width * min(1.0, ct / dur))
    return "█" * filled + "░" * (width - filled)


# ── 화면 그리기 ──────────────────────────────────────────────────────────────

def draw(scr, s, last_ref, msg, msg_until):
    scr.erase()
    h, w = scr.getmaxyx()
    now_s = datetime.now().strftime("%H:%M:%S")

    C_HDR  = curses.color_pair(1)              # cyan  — 헤더/구분선
    C_OK   = curses.color_pair(2)              # green — 정상/재생 중
    C_WARN = curses.color_pair(3)              # yellow — 경고/일시정지
    DIM    = curses.A_DIM

    def wr(row, col, text, attr=0):
        if row < 0 or row >= h or col < 0 or col >= w:
            return
        try:
            scr.addstr(row, col, str(text)[:max(0, w - col - 1)], attr)
        except curses.error:
            pass

    # ── 타이틀 바 ─────────────────────────────────────────────────────────────
    wr(0, 0, "─" * (w - 1), C_HDR)
    title = "  Kids Kiosk Control  "
    wr(0, max(0, (w - _dw(title)) // 2), title, C_HDR | curses.A_BOLD)
    wr(0, max(0, w - _dw(now_s) - 2), now_s, C_HDR)

    r = 2

    # ── 키오스크 상태 ──────────────────────────────────────────────────────────
    if s["running"]:
        wr(r, 2, "● RUNNING", C_OK | curses.A_BOLD)
        wr(r, 14, f"(Firefox PID {s['ff_pid']})", DIM)
    elif s["endscreen"]:
        wr(r, 2, "⏹  END SCREEN", C_WARN | curses.A_BOLD)
        wr(r, 18, f"(PID {s['es_pid']}) — 시청 시간 종료", DIM)
    else:
        wr(r, 2, "○ STOPPED", C_WARN | curses.A_BOLD)
    r += 1

    # Marionette + 자동재생
    m_str = "Marionette ✓" if s["marionette"] else "Marionette ✗"
    wr(r, 2, m_str, C_OK if s["marionette"] else C_WARN)
    ap_str = "  자동재생 ON" if s["autoplay"] else "  자동재생 OFF"
    if s["ap_pid"]:
        ap_str += f" (PID {s['ap_pid']})"
    wr(r, 2 + len(m_str), ap_str)
    r += 1

    # 입력 잠금 상태 (kb-grabber 가 살아 있을 때만)
    if s["running"] and s["grab_pid"]:
        def lock_lbl(name, v):
            return f"{name} " + ("?" if v is None else "잠금 🔒" if v else "해제 🔓")
        kb_s = lock_lbl("키보드", s["kb_locked"])
        ms_s = lock_lbl("마우스", s["mouse_locked"])
        wr(r, 2, kb_s, C_OK if s["kb_locked"] else C_WARN)
        wr(r, 2 + _dw(kb_s) + 3, ms_s, C_OK if s["mouse_locked"] else C_WARN)
        r += 1
    r += 1

    # 세션 남은 시간
    if s["remaining"] is not None:
        mins, secs = divmod(int(s["remaining"]), 60)
        attr = C_WARN if s["remaining"] < 120 else 0
        wr(r, 2, f"세션 남은 시간: {mins}분 {secs:02d}초", attr)
        r += 1
    r += 1  # 빈 줄

    # ── 영상 정보 ──────────────────────────────────────────────────────────────
    if s["url"]:
        if s["vid_id"]:
            wr(r, 2, f"영상 ID: {s['vid_id']}")
        else:
            wr(r, 2, "홈 화면 (썸네일 탐색 중)", DIM)
        r += 1

        if s["paused"] is not None:
            icon = "⏸  일시정지" if s["paused"] else "▶  재생 중 "
            attr = C_WARN if s["paused"] else C_OK
            wr(r, 2, icon, attr | curses.A_BOLD)
            r += 1

        if s["dur"]:
            bar_w = min(30, w - 38)
            bar   = pbar(s["ct"], s["dur"], bar_w)
            pct   = min(100, s["ct"] / s["dur"] * 100)
            wr(r, 2, f"[{bar}] {fmt_dur(s['ct'])} / {fmt_dur(s['dur'])}  ({pct:.0f}%)")
            r += 1

        if s["pool"]:
            wr(r, 2, f"예약 풀: {s['pool']}개", DIM)
            r += 1

        if s["age"] and s["age"] > 8:
            wr(r, 2, f"⚠ 상태 정보 {s['age']:.0f}초 전", C_WARN)
            r += 1

    elif s["running"]:
        ctrl_note = ("자동재생 비활성 — skip/pause/fullscreen 불가"
                     if not s["ap_pid"] else "로딩 중...")
        wr(r, 2, f"(영상 정보 없음 — {ctrl_note})", DIM)

    # ── 메시지 ────────────────────────────────────────────────────────────────
    if msg and time.time() < msg_until:
        wr(max(r + 1, h - 6), 2, msg, C_WARN | curses.A_BOLD)

    # ── 푸터 (두 줄) ───────────────────────────────────────────────────────────
    if s["running"]:
        ROW1 = [
            ("[s] 다음 영상",     bool(s["ap_pid"])),
            ("[p] 일시정지/재생", bool(s["ap_pid"])),
            ("[f] 전체화면",      bool(s["ap_pid"])),
            ("[=] +5분",          True),
            ("[-] -5분",          True),
        ]
        ROW2 = [
            ("[k] 키보드 잠금",   bool(s["grab_pid"])),
            ("[m] 마우스 잠금",   bool(s["grab_pid"])),
            ("[,] 음량-",         True),
            ("[.] 음량+",         True),
            ("[/] 음소거",        True),
        ]
        ROW3 = [
            ("[Q] 키오스크 종료", True),
            ("[r] 새로고침",      True),
            ("[q] UI 종료",       True),
        ]
    elif s["endscreen"]:
        ROW1 = [
            ("[e] 시간 연장 + 재시작", True),
            ("[d] 종료화면 닫기",      True),
        ]
        ROW2 = [("[r] 새로고침", True), ("[q] UI 종료", True)]
        ROW3 = []
    else:
        ROW1 = [("[o] 키오스크 시작", True)]
        ROW2 = [("[r] 새로고침", True), ("[q] UI 종료", True)]
        ROW3 = []
    frow = h - 5
    try:
        scr.addstr(frow, 0, "─" * (w - 1), C_HDR)
    except curses.error:
        pass
    for ri, row in enumerate((ROW1, ROW2, ROW3), start=1):
        col = 1
        for label, active in row:
            attr = 0 if active else DIM
            try:
                scr.addstr(frow + ri, col, label + "  ", attr)
            except curses.error:
                break
            col += _dw(label) + 2

    ref_str = f"갱신 {int(time.time() - last_ref)}초 전  (자동 {REFRESH}s)"
    wr(frow + 4, 1, ref_str, DIM)

    scr.refresh()


def confirm_kill(scr):
    h, w = scr.getmaxyx()
    msg = " 키오스크를 종료하시겠습니까? [y/N] "
    try:
        scr.addstr(h // 2, max(0, (w - len(msg)) // 2), msg, curses.A_REVERSE)
        scr.refresh()
    except curses.error:
        pass
    scr.nodelay(False)
    ch = scr.getch()
    scr.nodelay(True)
    return ch in (ord("y"), ord("Y"))


# ── 메인 루프 ────────────────────────────────────────────────────────────────

def run(scr):
    curses.curs_set(0)
    scr.nodelay(True)
    scr.timeout(250)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN,   -1)
    curses.init_pair(2, curses.COLOR_GREEN,  -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)

    s         = gather()
    last_ref  = time.time()
    msg       = ""
    msg_until = 0

    while True:
        now = time.time()
        if now - last_ref >= REFRESH:
            s = gather()
            last_ref = now

        draw(scr, s, last_ref, msg, msg_until)
        ch = scr.getch()
        if ch == -1:
            continue

        if ch == ord("q"):
            break
        elif ch == ord("r"):
            s = gather()
            last_ref = time.time()
        elif ch == ord("s") and s["running"] and s["ap_pid"]:
            send_cmd("skip")
            msg, msg_until = "→ 다음 영상으로 건너뜁니다", time.time() + 3
        elif ch == ord("p") and s["running"] and s["ap_pid"]:
            cmd   = "resume" if s.get("paused") else "pause"
            label = "재생" if cmd == "resume" else "일시정지"
            send_cmd(cmd)
            msg, msg_until = f"→ {label} 명령 전달됨", time.time() + 3
        elif ch == ord("f") and s["running"] and s["ap_pid"]:
            send_cmd("fullscreen")
            msg, msg_until = "→ 전체화면 명령 전달됨", time.time() + 3
        elif ch in (ord("="), ord("+")) and s["running"]:
            result = adjust_time(+5)
            msg, msg_until = result, time.time() + 4
            s = gather(); last_ref = time.time()
        elif ch == ord("-") and s["running"]:
            result = adjust_time(-5)
            msg, msg_until = result, time.time() + 4
            s = gather(); last_ref = time.time()
        elif ch == ord("o") and not s["running"]:
            params = ask_start_params(scr)
            if params:
                minutes, autoplay, sat = params
                start_kiosk(minutes, autoplay, sat)
                label = "ON" if autoplay else "OFF"
                msg = f"→ 키오스크 시작 ({minutes}분, 자동재생 {label}) — 잠시 기다려주세요"
                msg_until = time.time() + 8
                time.sleep(1.5)
                s = gather(); last_ref = time.time()
        elif ch == ord("k") and s["running"] and s["grab_pid"]:
            ok = toggle_lock(s["grab_pid"], signal.SIGUSR1)
            time.sleep(0.3)  # 그랩버가 상태 파일을 갱신할 시간
            s = gather(); last_ref = time.time()
            if ok:
                state = "잠금" if s["kb_locked"] else "해제"
                msg = f"→ 키보드 {state}"
            else:
                msg = "토글 실패 — kb-grabber 프로세스 없음"
            msg_until = time.time() + 3
        elif ch == ord("m") and s["running"] and s["grab_pid"]:
            ok = toggle_lock(s["grab_pid"], signal.SIGUSR2)
            time.sleep(0.3)
            s = gather(); last_ref = time.time()
            if ok:
                state = "잠금" if s["mouse_locked"] else "해제"
                msg = f"→ 마우스 {state}"
            else:
                msg = "토글 실패 — kb-grabber 프로세스 없음"
            msg_until = time.time() + 3
        elif ch == ord("Q") and s["running"]:
            if confirm_kill(scr):
                kill_kiosk()
                msg, msg_until = "→ 키오스크 종료 신호 전달됨", time.time() + 5
                s = gather()
                last_ref = time.time()
        elif ch == ord(",") and s["running"]:
            msg, msg_until = adjust_volume("-5%"), time.time() + 2
        elif ch == ord(".") and s["running"]:
            msg, msg_until = adjust_volume("+5%"), time.time() + 2
        elif ch == ord("/") and s["running"]:
            msg, msg_until = toggle_mute(), time.time() + 2
        elif ch == ord("d") and s["endscreen"]:
            dismiss_endscreen()
            msg, msg_until = "→ 종료화면 닫기 — 잠시 기다려주세요", time.time() + 5
            time.sleep(0.8)
            s = gather(); last_ref = time.time()
        elif ch == ord("e") and s["endscreen"]:
            result = extend_session(scr)
            if result:
                minutes, autoplay, _ = result
                label = "ON" if autoplay else "OFF"
                msg = f"→ 재시작: {minutes}분, 자동재생 {label} — 잠시 기다려주세요"
                msg_until = time.time() + 8
            else:
                msg, msg_until = "취소됨", time.time() + 2
            time.sleep(1.5)
            s = gather(); last_ref = time.time()


def _ensure_tmux():
    """tmux 세션 'kids-control' 에 접속하거나 새로 만든다.
    - 이미 tmux 안: 그냥 진행 (재귀 방지)
    - 세션 있음: attach → 현재 프로세스가 tmux 로 교체됨 (return 없음)
    - 세션 없음: new-session → 현재 프로세스가 tmux 로 교체됨 (return 없음)
    - tmux 미설치: flock 폴백으로 진행
    """
    if os.environ.get("TMUX"):
        return  # 이미 tmux 안 → 정상 진행

    if subprocess.run(["which", "tmux"], capture_output=True).returncode != 0:
        return  # tmux 없음 → flock 폴백

    has = subprocess.run(["tmux", "has-session", "-t", TMUX_SESSION],
                         capture_output=True).returncode == 0
    if has:
        # 기존 세션에 접속 — os.execlp 는 현재 프로세스를 tmux 로 교체
        os.execlp("tmux", "tmux", "attach-session", "-t", TMUX_SESSION)
    else:
        # 새 세션 생성 후 이 스크립트를 그 안에서 실행
        os.execlp("tmux", "tmux", "new-session", "-s", TMUX_SESSION,
                  sys.executable, os.path.abspath(__file__))


def main():
    locale.setlocale(locale.LC_ALL, "")  # UTF-8 환경 보장 (한글 표시)
    _ensure_tmux()  # 항상 tmux 세션 안에서 실행 (tmux 있을 때)

    # tmux 없는 환경의 폴백: flock 으로 단일 인스턴스 강제
    if not _acquire_lock():
        try:
            pid = open(LOCK_FILE).read().strip()
        except Exception:
            pid = "?"
        print(f"kids-control 이 이미 실행 중입니다. (PID {pid})", file=sys.stderr)
        sys.exit(1)
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
