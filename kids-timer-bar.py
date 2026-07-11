#!/usr/bin/env python3
"""
화면 상단 타임리미트 바 - 남은 시청 시간을 시각적으로 표시
usage: kids-timer-bar.py <minutes>
       kids-timer-bar.py --end-ts <timestamp>

일시정지 감지 시 카운터 정지 + /tmp/kids-blackout-enabled 존재 시 화면 암전.
"""
import json, os, signal, subprocess, sys, time
import tkinter as tk
from Xlib.display import Display as XDisplay
from Xlib import Xatom

STATUS_FILE   = "/tmp/kids-autoplay-status.json"
STATE_FILE    = "/tmp/kids-kiosk-state.json"
TIMER_PIDFILE = "/tmp/kids-kiosk-timer.pid"
TIMER_FLAG    = "/tmp/kids-timer-ended"
BLACKOUT_FILE = "/tmp/kids-blackout-enabled"


def set_always_on_top(root):
    d = XDisplay()
    xwin = d.create_resource_object('window', root.winfo_id())
    for attr, val in [
        ('_NET_WM_WINDOW_TYPE', '_NET_WM_WINDOW_TYPE_DOCK'),
        ('_NET_WM_STATE',       '_NET_WM_STATE_ABOVE'),
    ]:
        try:
            a = d.intern_atom(attr)
            v = d.intern_atom(val)
            xwin.change_property(a, Xatom.ATOM, 32, [v])
        except Exception:
            pass
    d.flush()


def bar_color(ratio):
    if ratio > 0.5:
        r = int(60 + 40 * (1 - ratio) * 2)
        g, b = 200, 80
    else:
        r = 220
        g = int(200 * ratio * 2)
        b = 40
    return f'#{min(255,max(0,r)):02x}{min(255,max(0,g)):02x}{min(255,max(0,b)):02x}'


def _read_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _extend_kill_timer(new_end):
    """기존 kill 타이머를 종료하고 new_end 기준으로 재시작."""
    try:
        old_pid = int(open(TIMER_PIDFILE).read().strip())
        os.kill(old_pid, signal.SIGTERM)
    except Exception:
        pass
    new_secs = max(1, int(new_end - time.time()))
    proc = subprocess.Popen(
        ["bash", "-c",
         f"sleep {new_secs} && touch {TIMER_FLAG} && pkill -f youtubekids.com"],
        close_fds=True, start_new_session=True,
    )
    try:
        with open(TIMER_PIDFILE, "w") as f:
            f.write(str(proc.pid))
    except Exception:
        pass


def _update_state_end_ts(new_end):
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    state["end_ts"] = new_end
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    if sys.argv[1] == "--end-ts":
        end_ts    = float(sys.argv[2])
        total_sec = max(1.0, end_ts - time.time())
    else:
        total_sec = int(sys.argv[1]) * 60
        end_ts    = time.time() + total_sec

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes('-topmost', True)

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    H  = 22
    root.geometry(f'{sw}x{H}+0+0')
    root.configure(bg='#111827')

    cv = tk.Canvas(root, width=sw, height=H, bg='#111827', highlightthickness=0)
    cv.pack(fill='both', expand=True)

    root.update()
    set_always_on_top(root)

    blackout_win = None
    was_paused   = False
    pause_start  = None

    def show_blackout():
        nonlocal blackout_win
        if blackout_win is not None:
            return
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes('-topmost', True)
        # 타이머 바 아래부터 화면 끝까지 검게 덮음
        w.geometry(f'{sw}x{sh - H}+0+{H}')
        w.configure(bg='black')
        w.update()
        blackout_win = w

    def hide_blackout():
        nonlocal blackout_win
        if blackout_win is not None:
            blackout_win.destroy()
            blackout_win = None

    def tick():
        nonlocal end_ts, total_sec, blackout_win, was_paused, pause_start

        now    = time.time()
        status = _read_status()

        # 10초 이상 오래된 상태 파일은 무시 (autoplay 미실행 시 오판 방지)
        status_fresh = (now - status.get("ts", 0)) < 10
        is_paused    = status_fresh and status.get("state") == "paused"

        # 재개 전환: 정지된 시간만큼 end_ts 연장
        if was_paused and not is_paused and pause_start is not None:
            pause_dur = now - pause_start
            if pause_dur > 1.0:
                new_end    = end_ts + pause_dur
                end_ts     = new_end
                total_sec += pause_dur
                _update_state_end_ts(new_end)
                _extend_kill_timer(new_end)
            pause_start = None

        # 일시정지 전환
        if not was_paused and is_paused:
            pause_start = now

        was_paused = is_paused

        # 암전 처리 (파일 존재 여부로 동적 토글)
        if is_paused and os.path.exists(BLACKOUT_FILE):
            show_blackout()
        else:
            hide_blackout()

        # 남은 시간 (일시정지 중엔 pause_start 기준으로 freeze)
        if is_paused and pause_start is not None:
            rem = max(0.0, end_ts - pause_start)
        else:
            rem = max(0.0, end_ts - now)

        ratio = rem / total_sec if total_sec > 0 else 0.0
        bw    = int(sw * ratio)

        cv.delete('all')
        if bw > 0:
            cv.create_rectangle(0, 0, bw, H, fill=bar_color(ratio), outline='')

        m, s = int(rem) // 60, int(rem) % 60
        pause_icon = " ⏸" if is_paused else ""
        cv.create_text(sw // 2, H // 2,
                       text=f'{m}:{s:02d}{pause_icon}',
                       fill='white', font=('Sans', 11, 'bold'))

        root.lift()
        root.after(1000, tick)

    tick()
    root.mainloop()


if __name__ == '__main__':
    main()
