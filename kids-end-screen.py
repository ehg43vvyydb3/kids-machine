#!/usr/bin/env python3
"""
시청 종료 화면 - 키보드 그랩 유지, Ctrl+Alt+Q 로만 닫힘
"""
import signal
import tkinter as tk
from Xlib import X, display as xdisplay, XK


class EndScreen:
    def __init__(self):
        self.d = xdisplay.Display()
        self.ctrl = False
        self.alt = False
        self.grabbed = False

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
            elif ev.type == X.KeyRelease:
                ks = self.d.keycode_to_keysym(ev.detail, 0)
                if ks in (XK.XK_Control_L, XK.XK_Control_R):
                    self.ctrl = False
                elif ks in (XK.XK_Alt_L, XK.XK_Alt_R):
                    self.alt = False
        self.win.after(100, self._poll)

    def _exit(self):
        if self.grabbed:
            try:
                self.d.ungrab_keyboard(X.CurrentTime)
                self.d.flush()
            except Exception:
                pass
        self.win.quit()

    def run(self):
        self.win.mainloop()


EndScreen().run()
