#!/usr/bin/env python3
"""
키보드 전체 그랩 (영상 시청 중) - Ctrl+Alt+K 로 즉시 전체 종료
"""
import sys, signal, subprocess, datetime, os
from Xlib import X, display as xdisplay, XK
from Xlib.ext.xtest import fake_input

# XF86 볼륨 keysym 값 (fn+좌우 방향키)
_VOL_LOWER = 0x1008FF11  # XF86AudioLowerVolume
_VOL_RAISE = 0x1008FF13  # XF86AudioRaiseVolume
_VOL_MUTE  = 0x1008FF12  # XF86AudioMute

def _vol(delta_str):
    subprocess.Popen(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', delta_str],
                     close_fds=True)

_display = None

def _grab():
    if _display:
        _display.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)
        _display.flush()

def ungrab():
    if _display:
        try:
            _display.ungrab_keyboard(X.CurrentTime)
            _display.flush()
        except Exception:
            pass

def forward_key(keycode):
    """f / Esc 등 허용된 키를 Firefox로 전달.
    전체 그랩 중엔 우리가 가로채므로, 잠깐 그랩을 풀고 같은 키를
    XTEST로 재주입한 뒤 다시 그랩한다(아이가 그 찰나에 끼어들 수 없음)."""
    try:
        ungrab()
        fake_input(_display, X.KeyPress, keycode)
        _display.sync()
        fake_input(_display, X.KeyRelease, keycode)
        _display.sync()
    finally:
        _grab()

def on_signal(signum, frame):
    ungrab()
    sys.exit(0)

def main():
    global _display
    _display = xdisplay.Display()
    root = _display.screen().root

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    if root.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime) != X.GrabSuccess:
        print("keyboard grab failed", file=sys.stderr)
        sys.exit(1)
    _display.flush()

    ctrl = False
    alt = False

    try:
        while True:
            ev = _display.next_event()
            if ev.type == X.KeyPress:
                ks = _display.keycode_to_keysym(ev.detail, 0)
                if ks in (XK.XK_Control_L, XK.XK_Control_R):
                    ctrl = True
                elif ks in (XK.XK_Alt_L, XK.XK_Alt_R):
                    alt = True
                elif ks == _VOL_RAISE:
                    _vol('+5%')
                elif ks == _VOL_LOWER:
                    _vol('-5%')
                elif ks == _VOL_MUTE:
                    subprocess.Popen(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', 'toggle'],
                                     close_fds=True)
                elif ks == XK.XK_p:
                    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                    dl = os.path.expanduser('~/Downloads')
                    os.makedirs(dl, exist_ok=True)
                    path = f'{dl}/yt-kids-{ts}.png'
                    for cmd in [['scrot', path],
                                ['import', '-window', 'root', path],
                                ['xfce4-screenshooter', '-f', '-s', path]]:
                        if subprocess.run(['which', cmd[0]],
                                          capture_output=True).returncode == 0:
                            subprocess.Popen(cmd)
                            break
                elif ks in (XK.XK_f, XK.XK_Escape):
                    # 전체화면 토글(f)·해제(Esc)는 그랩에서 풀어 Firefox로 전달
                    forward_key(ev.detail)
                elif ks == XK.XK_k and ctrl and alt:
                    # 비상탈출: 키보드 해제 + Firefox 종료 (타이머는 main script가 kill)
                    ungrab()
                    subprocess.run(['pkill', '-f', 'youtubekids.com'], check=False)
                    sys.exit(0)
            elif ev.type == X.KeyRelease:
                ks = _display.keycode_to_keysym(ev.detail, 0)
                if ks in (XK.XK_Control_L, XK.XK_Control_R):
                    ctrl = False
                elif ks in (XK.XK_Alt_L, XK.XK_Alt_R):
                    alt = False
    except Exception:
        ungrab()

if __name__ == '__main__':
    main()
