#!/usr/bin/env python3
"""
화면 상단 타임리미트 바 - 남은 시청 시간을 시각적으로 표시
usage: kids-timer-bar.py <minutes>
"""
import sys
import time
import tkinter as tk
from Xlib.display import Display as XDisplay
from Xlib import Xatom


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


def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    # '--end-ts <timestamp>' 모드: kids-control.py 가 시간 조정 후 재시작할 때 사용
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
    H = 22
    root.geometry(f'{sw}x{H}+0+0')
    root.configure(bg='#111827')

    cv = tk.Canvas(root, width=sw, height=H, bg='#111827', highlightthickness=0)
    cv.pack(fill='both', expand=True)

    root.update()
    set_always_on_top(root)

    def tick():
        rem = max(0.0, end_ts - time.time())
        ratio = rem / total_sec
        bw = int(sw * ratio)

        cv.delete('all')
        if bw > 0:
            cv.create_rectangle(0, 0, bw, H, fill=bar_color(ratio), outline='')

        m, s = int(rem) // 60, int(rem) % 60
        cv.create_text(sw // 2, H // 2,
                       text=f'{m}:{s:02d}',
                       fill='white', font=('Sans', 11, 'bold'))

        root.lift()
        root.after(1000, tick)

    tick()
    root.mainloop()


if __name__ == '__main__':
    main()
