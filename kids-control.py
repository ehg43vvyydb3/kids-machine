#!/usr/bin/env python3
"""
kids-control.py — SSH 터미널에서 YouTube Kids 키오스크를 모니터링/조작

사용법: python3 /home/jjejje/kids-machine/kids-control.py
종료:  q  (키오스크에는 영향 없음)

제어 명령은 /tmp/kids-autoplay-cmd 파일을 통해 kids-autoplay.py 로 전달된다.
kids-autoplay.py 가 살아 있을 때만 skip/pause/resume/fullscreen 이 동작한다.
"""
import curses, json, os, socket, subprocess, sys, time
from datetime import datetime

STATE_FILE       = "/tmp/kids-kiosk-state.json"
STATUS_FILE      = "/tmp/kids-autoplay-status.json"
CMD_FILE         = "/tmp/kids-autoplay-cmd"
AUTOPLAY_PIDFILE = "/tmp/kids-autoplay.pid"

REFRESH = 2  # 자동 갱신 주기(초)


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

    # ── 푸터 ──────────────────────────────────────────────────────────────────
    KEYS = [
        ("[s] 다음 영상",     s["running"] and bool(s["ap_pid"])),
        ("[p] 일시정지/재생", s["running"] and bool(s["ap_pid"])),
        ("[f] 전체화면",      s["running"] and bool(s["ap_pid"])),
        ("[k] 키오스크 종료", s["running"]),
        ("[r] 새로고침",      True),
        ("[q] UI 종료",       True),
    ]
    frow = h - 3
    try:
        scr.addstr(frow, 0, "─" * (w - 1), C_HDR)
    except curses.error:
        pass
    col = 1
    for label, active in KEYS:
        attr = 0 if active else DIM
        try:
            scr.addstr(frow + 1, col, label + "  ", attr)
        except curses.error:
            break
        col += len(label) + 2

    ref_str = f"갱신 {int(time.time() - last_ref)}초 전  (자동 {REFRESH}s)"
    wr(frow + 2, 1, ref_str, DIM)

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
        elif ch == ord("k") and s["running"]:
            if confirm_kill(scr):
                kill_kiosk()
                msg, msg_until = "→ 키오스크 종료 신호 전달됨", time.time() + 5
                s = gather()
                last_ref = time.time()


def main():
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
