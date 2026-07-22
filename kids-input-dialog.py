#!/usr/bin/env python3
"""
시청 시간 입력 + 자동재생 체크박스 + 채도 슬라이더 다이얼로그
stdout: "분,True|False,채도(0-100)"  exitcode: 0=확인 1=취소
"""
import tkinter as tk
import sys
import os

root = tk.Tk()
# 제목은 온라인/오프라인 공용이라 env 로 바꿀 수 있게 한다.
# 기본값(온라인)은 그대로, 오프라인 세션(kids-offline.sh)은 " - 오프라인" 을 붙인다.
root.title(os.environ.get("KIDS_DIALOG_TITLE", "유튜브 키즈"))
root.resizable(False, False)
root.update_idletasks()
w, h = 320, 230
x = (root.winfo_screenwidth() - w) // 2
y = (root.winfo_screenheight() - h) // 2
root.geometry(f"{w}x{h}+{x}+{y}")

result = [None]

frame = tk.Frame(root, padx=24, pady=18)
frame.pack(fill='both', expand=True)

tk.Label(frame, text="시청 시간 (분):").grid(row=0, column=0, sticky='w', pady=6)
entry = tk.Entry(frame, width=8)
entry.insert(0, "30")
entry.grid(row=0, column=1, sticky='w', padx=10)
entry.select_range(0, 'end')
entry.focus()

autoplay_var = tk.BooleanVar(value=True)
tk.Checkbutton(frame, text="영상 자동 시작", variable=autoplay_var).grid(
    row=1, column=0, columnspan=2, sticky='w', pady=4)

sat_frame = tk.Frame(frame)
sat_frame.grid(row=2, column=0, columnspan=2, sticky='ew', pady=4)
tk.Label(sat_frame, text="화면 채도:").pack(side='left')
sat_var = tk.IntVar(value=100)
sat_label = tk.Label(sat_frame, text="100%", width=4)
sat_label.pack(side='right')
def on_sat_change(val):
    sat_label.config(text=f"{int(float(val))}%")
sat_slider = tk.Scale(sat_frame, from_=0, to=100, orient='horizontal',
                      variable=sat_var, showvalue=False, command=on_sat_change,
                      length=180)
sat_slider.pack(side='left', padx=6)

def confirm(event=None):
    val = entry.get().strip()
    if val.isdigit() and int(val) > 0:
        result[0] = f"{val},{autoplay_var.get()},{sat_var.get()}"
        root.quit()

def cancel(event=None):
    root.quit()

btn_frame = tk.Frame(frame)
btn_frame.grid(row=3, column=0, columnspan=2, pady=12)
tk.Button(btn_frame, text="시작", command=confirm, width=8).pack(side='left', padx=5)
tk.Button(btn_frame, text="취소", command=cancel, width=8).pack(side='left', padx=5)

entry.bind('<Return>', confirm)
root.bind('<Escape>', cancel)
root.protocol("WM_DELETE_WINDOW", cancel)
root.mainloop()

if result[0]:
    print(result[0])
    sys.exit(0)
else:
    sys.exit(1)
