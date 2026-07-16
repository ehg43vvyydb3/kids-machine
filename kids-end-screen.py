#!/usr/bin/env python3
"""
시청 종료 화면 - 키보드 그랩 유지, Ctrl+Alt+Q 로만 닫힘
Ctrl+Alt+H 로 일일 시청 기록(날짜별)을 화면에서 바로 볼 수 있다.
"""
import json
import signal
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from Xlib import X, display as xdisplay, XK

AUTO_POWEROFF = "--poweroff" in sys.argv[1:]
DAILY_FILE    = "/home/jjejje/.kids-daily-watch.json"  # kids-autoplay.py 가 기록


def _load_daily_history():
    """kids-autoplay.py 가 쓰는 {"YYYY-MM-DD": 누적초} 형식을 읽는다.
    예전 형식({"date":..,"seconds":..})이면 오늘 값만 승계해 마이그레이션한다."""
    try:
        with open(DAILY_FILE) as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    if set(data.keys()) <= {"date", "seconds"}:
        d = data.get("date")
        return {d: data.get("seconds", 0)} if d else {}
    return data


def _fmt_hm(secs):
    total_min = int(secs) // 60
    h, m = divmod(total_min, 60)
    return f"{h}시간 {m}분" if h else f"{m}분"


class EndScreen:
    def __init__(self):
        self.d = xdisplay.Display()
        self.ctrl = False
        self.alt = False
        self.grabbed = False
        self.history_win = None

        self.win = tk.Tk()
        self.win.title("")
        self.win.configure(bg='#111827')
        self.win.overrideredirect(True)
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        self.win.geometry(f"{sw}x{sh}+0+0")
        self.win.attributes('-topmost', True)
        self.win.lift()
        self.win.focus_force()
        self._build_ui()

        self._grab_keyboard()

        signal.signal(signal.SIGCONT, self._handle_sigcont)
        # 원격 제어(kids-control.py)에서 SIGTERM 으로 종료 요청 시 그랩을 해제하고 닫는다
        signal.signal(signal.SIGTERM, lambda sig, frm: self.win.after(0, self._exit))

        self.win.after(100, self._poll)
        self.win.after(3000, self._heartbeat)
        if AUTO_POWEROFF:
            self.win.after(3000, self._auto_poweroff)

    def _grab_keyboard(self):
        try:
            if self.grabbed:
                self.d.ungrab_keyboard(X.CurrentTime)
                self.d.flush()
        except Exception:
            pass
        xroot = self.d.screen().root
        result = xroot.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)
        self.d.flush()
        self.grabbed = (result == X.GrabSuccess)

    def _build_ui(self):
        frame = tk.Frame(self.win, bg='#111827')
        frame.place(relx=0.5, rely=0.5, anchor='center')

        tk.Label(frame,
                 text="오늘 유튜브는 여기까지!",
                 font=('Sans', 52, 'bold'),
                 fg='#f9fafb', bg='#111827').pack(pady=(0, 16))

        tk.Label(frame,
                 text="내일 또 봐요",
                 font=('Sans', 32),
                 fg='#6b7280', bg='#111827').pack()

        today = datetime.now().strftime("%Y-%m-%d")
        today_secs = _load_daily_history().get(today, 0)
        tk.Label(frame,
                 text=f"오늘 총 {_fmt_hm(today_secs)} 시청했어요",
                 font=('Sans', 22),
                 fg='#9ca3af', bg='#111827').pack(pady=(24, 0))

        tk.Label(self.win,
                 text="Ctrl+Alt+H: 지난 시청 기록 보기",
                 font=('Sans', 12),
                 fg='#374151', bg='#111827').place(relx=0.5, rely=0.96, anchor='center')

    def _toggle_history(self):
        if self.history_win is not None:
            self.history_win.destroy()
            self.history_win = None
            return

        days = sorted(_load_daily_history().items(), reverse=True)[:14]
        today = datetime.now().strftime("%Y-%m-%d")

        win = tk.Toplevel(self.win, bg='#111827')
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        w, h = 420, 60 + 34 * max(1, len(days))
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        tk.Label(win, text="일일 시청 기록", font=('Sans', 20, 'bold'),
                 fg='#f9fafb', bg='#111827').pack(pady=(16, 8))

        if not days:
            tk.Label(win, text="기록이 없습니다.", font=('Sans', 14),
                     fg='#9ca3af', bg='#111827').pack()
        else:
            for date, secs in days:
                tag = "  (오늘)" if date == today else ""
                tk.Label(win, text=f"{date}   {_fmt_hm(secs)}{tag}",
                         font=('Sans', 14),
                         fg='#e5e7eb', bg='#111827').pack(anchor='center')

        win.update()
        self.history_win = win

    def _handle_sigcont(self, signum, frame):
        # 시그널 핸들러에서는 tkinter를 직접 호출하면 안 되므로 after로 위임
        self.win.after(0, self._on_resume)

    def _on_resume(self):
        # suspend 에서 깨어날 때 창과 키보드 그랩 재설정
        self.win.lift()
        self.win.attributes('-topmost', True)
        self.win.focus_force()
        self.win.update()
        self._grab_keyboard()

    def _heartbeat(self):
        # 그랩이 풀렸으면 재시도 (suspend/resume 대응)
        if not self.grabbed:
            self._on_resume()
        self.win.after(3000, self._heartbeat)

    def _poll(self):
        while self.d.pending_events():
            ev = self.d.next_event()
            if ev.type == X.KeyPress:
                ks = self.d.keycode_to_keysym(ev.detail, 0)
                if ks in (XK.XK_Control_L, XK.XK_Control_R):
                    self.ctrl = True
                elif ks in (XK.XK_Alt_L, XK.XK_Alt_R):
                    self.alt = True
                elif ks == XK.XK_q and self.ctrl and self.alt:
                    self._exit()
                    return
                elif ks == XK.XK_h and self.ctrl and self.alt:
                    self._toggle_history()
            elif ev.type == X.KeyRelease:
                ks = self.d.keycode_to_keysym(ev.detail, 0)
                if ks in (XK.XK_Control_L, XK.XK_Control_R):
                    self.ctrl = False
                elif ks in (XK.XK_Alt_L, XK.XK_Alt_R):
                    self.alt = False
        self.win.after(100, self._poll)

    def _auto_poweroff(self):
        # 부모가 그 사이 [d]/Ctrl+Alt+Q로 이미 닫았으면(그랩 해제됨) 건너뛴다
        if self.grabbed:
            self._exit(poweroff=True)

    def _exit(self, poweroff=False):
        if self.grabbed:
            try:
                self.d.ungrab_keyboard(X.CurrentTime)
                self.d.flush()
            except Exception:
                pass
        self.grabbed = False
        if poweroff:
            try:
                subprocess.Popen(["systemctl", "poweroff"])
            except Exception:
                pass
        self.win.quit()

    def run(self):
        self.win.mainloop()


EndScreen().run()
