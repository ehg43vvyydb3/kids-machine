#!/usr/bin/env python3
"""
kids-control.py — SSH 터미널에서 YouTube Kids 키오스크를 모니터링/조작

사용법: python3 /home/jjejje/kids-machine/kids-control.py
종료:  q  (키오스크에는 영향 없음)

제어 명령은 /tmp/kids-autoplay-cmd 파일을 통해 kids-autoplay.py 로 전달된다.
kids-autoplay.py 가 살아 있을 때만 skip/pause/resume/fullscreen 이 동작한다.
"""
import curses, fcntl, json, os, signal, socket, subprocess, sys, time
from datetime import datetime

STATE_FILE       = "/tmp/kids-kiosk-state.json"
STATUS_FILE      = "/tmp/kids-autoplay-status.json"
CMD_FILE         = "/tmp/kids-autoplay-cmd"
AUTOPLAY_PIDFILE = "/tmp/kids-autoplay.pid"
TIMER_PIDFILE    = "/tmp/kids-kiosk-timer.pid"
TIMERBAR_PIDFILE = "/tmp/kids-timerbar.pid"
TIMERBAR_SCRIPT  = "/home/jjejje/kids-machine/kids-timer-bar.py"
KIOSK_SCRIPT     = "/home/jjejje/kids-machine/kids-kiosk.sh"
LOCK_FILE        = "/tmp/kids-control.lock"

REFRESH = 2  # 자동 갱신 주기(초)

_lock_fh = None  # 단일 인스턴스 잠금 파일 핸들 (GC 방지용)


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
    ff_pid = _ff_pid()
    ap_pid = _pid_alive(AUTOPLAY_PIDFILE)

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


def kill_kiosk():
    subprocess.run(["pkill", "-f", "youtubekids.com"], check=False)


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
    scr.addstr(0, max(0, (w - len(title)) // 2), title, C1 | curses.A_BOLD)
    scr.addstr(2, 2, "ESC: 취소   Enter: 확인", curses.A_DIM)

    def ask_int(row, prompt, default, lo, hi):
        scr.addstr(row, 2, prompt)
        scr.refresh()
        curses.curs_set(1)
        raw = _readline(scr, row, 2 + len(prompt), maxlen=4, default=str(default))
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
    scr.addstr(6, 2, "자동재생? [Y/n] : ")
    scr.refresh()
    autoplay = True
    while True:
        ch = scr.getch()
        if ch == 27:
            return None
        elif ch in (ord("n"), ord("N")):
            autoplay = False
            scr.addstr(6, 2 + len("자동재생? [Y/n] : "), "n")
            break
        elif ch in (ord("y"), ord("Y"), 10, 13):
            scr.addstr(6, 2 + len("자동재생? [Y/n] : "), "y")
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
    wr(0, max(0, (w - len(title)) // 2), title, C_HDR | curses.A_BOLD)
    wr(0, max(0, w - len(now_s) - 2), now_s, C_HDR)

    r = 2

    # ── 키오스크 상태 ──────────────────────────────────────────────────────────
    if s["running"]:
        wr(r, 2, "● RUNNING", C_OK | curses.A_BOLD)
        wr(r, 14, f"(Firefox PID {s['ff_pid']})", DIM)
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
    r += 2

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
            ("[=] +10분",         True),
            ("[-] -10분",         True),
        ]
        ROW2 = [
            ("[k] 키오스크 종료", True),
            ("[r] 새로고침",      True),
            ("[q] UI 종료",       True),
        ]
    else:
        ROW1 = [("[o] 키오스크 시작", True)]
        ROW2 = [("[r] 새로고침", True), ("[q] UI 종료", True)]
    frow = h - 4
    try:
        scr.addstr(frow, 0, "─" * (w - 1), C_HDR)
    except curses.error:
        pass
    col = 1
    for label, active in ROW1:
        attr = 0 if active else DIM
        try:
            scr.addstr(frow + 1, col, label + "  ", attr)
        except curses.error:
            break
        col += len(label) + 2
    col = 1
    for label, active in ROW2:
        attr = 0 if active else DIM
        try:
            scr.addstr(frow + 2, col, label + "  ", attr)
        except curses.error:
            break
        col += len(label) + 2

    ref_str = f"갱신 {int(time.time() - last_ref)}초 전  (자동 {REFRESH}s)"
    wr(frow + 3, 1, ref_str, DIM)

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
            result = adjust_time(+10)
            msg, msg_until = result, time.time() + 4
            s = gather(); last_ref = time.time()
        elif ch == ord("-") and s["running"]:
            result = adjust_time(-10)
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
        elif ch == ord("k") and s["running"]:
            if confirm_kill(scr):
                kill_kiosk()
                msg, msg_until = "→ 키오스크 종료 신호 전달됨", time.time() + 5
                s = gather()
                last_ref = time.time()


def main():
    if not _acquire_lock():
        try:
            pid = open(LOCK_FILE).read().strip()
        except Exception:
            pid = "?"
        print(f"kids-control 이 이미 실행 중입니다. (PID {pid})", file=sys.stderr)
        print("종료하려면 해당 터미널에서 q 를 누르세요.", file=sys.stderr)
        sys.exit(1)
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
