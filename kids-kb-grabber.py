#!/usr/bin/env python3
"""
키보드/마우스 그랩 (영상 시청 중)
  Ctrl+Alt+Q : 키오스크 종료
  Ctrl+Alt+K : 키보드 잠금 토글
  Ctrl+Alt+M : 마우스 잠금 토글
  Ctrl+Alt+S : 다음 영상 (kids-autoplay 에 skip 전달)
  Ctrl+Alt+, : 음량 내림
  Ctrl+Alt+. : 음량 올림
  Ctrl+Alt+/ : 음소거 토글
기본: 키보드 잠금 ON, 마우스 잠금 ON

원격 제어 (kids-control.py 가 시그널로 토글):
  SIGUSR1 : 키보드 잠금 토글
  SIGUSR2 : 마우스 잠금 토글
현재 잠금 상태는 /tmp/kids-grabber-state.json 으로 내보낸다.
"""
import re, sys, signal, select, subprocess, datetime, os, json, time, traceback
from Xlib import X, XK, error as Xerror, display as xdisplay
from Xlib.ext.xtest import fake_input

_VOL_LOWER = 0x1008FF11
_VOL_RAISE = 0x1008FF13
_VOL_MUTE  = 0x1008FF12

POINTER_IDFILE = '/tmp/kids-pointer-ids.txt'
STATE_FILE     = '/tmp/kids-grabber-state.json'
AUTOPLAY_CMD   = '/tmp/kids-autoplay-cmd'  # kids-autoplay.py 와 약속된 명령 파일

_display      = None
_root         = None
_kb_locked    = True
_mouse_locked = True
_hotkey_grabs = []  # (keycode, modmask) — 키보드 잠금 해제 시 개별 그랩
_pointer_ids  = set()  # 우리가 잠근 장치 ID — 복원용
_ctrl = False  # 잠금 중 모디파이어 추적 (시그널 토글 시 리셋 필요해 전역)
_alt  = False


def _save_state():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({'kb_locked': _kb_locked, 'mouse_locked': _mouse_locked}, f)
    except OSError:
        pass


def _vol(delta):
    subprocess.Popen(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', delta],
                     close_fds=True)


def _mute():
    subprocess.Popen(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', 'toggle'],
                     close_fds=True)


def _skip_video():
    """kids-autoplay.py 에 다음 영상으로 건너뛰라는 명령을 파일로 전달.
    (kids-control.py 의 send_cmd('skip') 과 동일한 메커니즘)"""
    try:
        with open(AUTOPLAY_CMD, 'w') as f:
            f.write('skip')
    except OSError:
        pass


def _attached_pointer_ids():
    """마스터에 연결된(=활성) slave pointer 의 ID 목록.
    disable 된 장치는 xinput list 에서 [floating slave] 로 표시되어
    여기서는 잡히지 않는다 — 복원은 _pointer_ids 에 기억해 둔 ID 로 한다."""
    r = subprocess.run(['xinput', 'list'], capture_output=True, text=True)
    ids = set()
    for line in r.stdout.splitlines():
        if 'XTEST' in line or 'master' in line:
            continue
        if 'slave' in line and 'pointer' in line:
            m = re.search(r'id=(\d+)', line)
            if m:
                ids.add(m.group(1))
    return ids


def _load_pointer_ids():
    """이전 실행(또는 비정상 종료)이 남긴 ID 파일을 읽어 복원 대상에 합친다."""
    try:
        with open(POINTER_IDFILE) as f:
            _pointer_ids.update(l.strip() for l in f if l.strip().isdigit())
    except OSError:
        pass


def _save_pointer_ids():
    """kids-kiosk.sh 가 그랩버 사후에도 복원할 수 있도록 ID 를 파일로 남긴다."""
    try:
        with open(POINTER_IDFILE, 'w') as f:
            f.write('\n'.join(sorted(_pointer_ids)) + '\n')
    except OSError:
        pass


def _set_mouse(locked):
    global _mouse_locked
    if locked:
        _pointer_ids.update(_attached_pointer_ids())
        _save_pointer_ids()
        for pid in _pointer_ids:
            subprocess.run(['xinput', 'disable', pid], check=False)
    else:
        for pid in _pointer_ids | _attached_pointer_ids():
            subprocess.run(['xinput', 'enable', pid], check=False)
    _mouse_locked = locked
    _save_state()


def _full_grab():
    result = _root.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync,
                                 X.CurrentTime)
    _display.flush()
    return result == X.GrabSuccess


def _ungrab_kb():
    try:
        _display.ungrab_keyboard(X.CurrentTime)
        _display.flush()
    except Exception:
        pass


def _grab_hotkeys():
    """키보드 잠금 해제 상태: 세 단축키만 XGrabKey로 가로챔.
    XGrabKey 는 모디파이어가 정확히 일치해야 하므로 NumLock(Mod2)/
    CapsLock(Lock) 이 켜진 조합도 함께 그랩한다."""
    global _hotkey_grabs
    _ungrab_hotkeys()
    base = X.ControlMask | X.Mod1Mask
    for ks in (XK.XK_q, XK.XK_k, XK.XK_m, XK.XK_s,
               XK.XK_comma, XK.XK_period, XK.XK_slash):
        kc = _display.keysym_to_keycode(ks)
        if not kc:
            continue
        for extra in (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask):
            mods = base | extra
            # 다른 클라이언트(WM 등)가 이미 그랩한 조합이면 BadAccess —
            # 무시하고 나머지 조합으로 동작한다
            _root.grab_key(kc, mods, False, X.GrabModeAsync, X.GrabModeAsync,
                           onerror=Xerror.CatchError(Xerror.BadAccess))
            _hotkey_grabs.append((kc, mods))
    _display.flush()


def _ungrab_hotkeys():
    global _hotkey_grabs
    for kc, mods in _hotkey_grabs:
        try:
            _root.ungrab_key(kc, mods)
        except Exception:
            pass
    _hotkey_grabs = []
    try:
        _display.flush()
    except Exception:
        pass


def _set_kb(locked):
    global _kb_locked, _ctrl, _alt
    if locked:
        _ungrab_hotkeys()
        _full_grab()
    else:
        _ungrab_kb()
        _grab_hotkeys()
    _kb_locked = locked
    _ctrl = _alt = False  # 잠금 전환 시 모디파이어 상태 초기화 (오작동 방지)
    _save_state()


def _reassert_locks():
    """suspend/resume 등으로 grab 이 풀리거나 포인터가 재열거됐을 때 잠금을
    다시 강제한다. 절전은 kids-kiosk.sh 가 막지만, 배터리 위급 절전처럼
    inhibitor 로 막을 수 없는 경우를 위한 보험이다."""
    if _kb_locked:
        _full_grab()  # 소유 클라이언트의 재grab 은 멱등 — 풀렸으면 다시 잡는다
    if _mouse_locked:
        # 잠금 중이면 평소엔 attached 가 비어 no-op. resume 으로 다시 살아난
        # (enabled) 포인터가 보이면 그 ID 를 기억하고 다시 disable 한다.
        new = _attached_pointer_ids()
        if new:
            _pointer_ids.update(new)
            _save_pointer_ids()
            for pid in new:
                subprocess.run(['xinput', 'disable', pid], check=False)


def _forward_key(keycode):
    """잠금 중 허용된 키를 Firefox로 전달 (잠깐 그랩 해제 후 재그랩)."""
    try:
        _ungrab_kb()
        fake_input(_display, X.KeyPress, keycode)
        _display.sync()
        fake_input(_display, X.KeyRelease, keycode)
        _display.sync()
    finally:
        _full_grab()


def _cleanup():
    _ungrab_kb()
    _ungrab_hotkeys()
    _set_mouse(False)
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def _cleanup_exit():
    _cleanup()
    sys.exit(0)


def _on_signal(signum, frame):
    _cleanup_exit()


_pending = []  # 시그널로 들어온 토글 요청 — 메인 루프에서 처리


def _on_toggle_kb(signum, frame):
    # 핸들러에서 동기 X 호출(grab_keyboard 응답 대기)을 하면 next_event 와
    # 소켓 읽기가 꼬이므로 요청만 쌓고 메인 루프에서 처리한다
    _pending.append('kb')


def _on_toggle_mouse(signum, frame):
    _pending.append('mouse')


def main():
    global _display, _root

    _display = xdisplay.Display()
    _root    = _display.screen().root

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)
    signal.signal(signal.SIGUSR1, _on_toggle_kb)     # kids-control: 키보드 토글
    signal.signal(signal.SIGUSR2, _on_toggle_mouse)  # kids-control: 마우스 토글

    # 기본: 키보드+마우스 잠금
    # (이전 실행이 잠근 채 죽었을 수 있으므로 ID 파일을 먼저 읽어 복원 대상에 포함)
    _load_pointer_ids()
    _set_mouse(True)
    if not _full_grab():
        print("keyboard grab failed", file=sys.stderr)
        _set_mouse(False)  # 마우스만 잠근 채 죽지 않도록 복원 후 종료
        sys.exit(1)

    global _ctrl, _alt

    _last_reassert = 0.0
    try:
        while True:
            # 주기적으로 잠금 재확인 (suspend/resume 후 grab·마우스 복구용)
            now = time.monotonic()
            if now - _last_reassert >= 2.0:
                _reassert_locks()
                _last_reassert = now

            # 원격 토글 요청 처리 (시그널 핸들러가 쌓은 것)
            while _pending:
                act = _pending.pop(0)
                if act == 'kb':
                    _set_kb(not _kb_locked)
                elif act == 'mouse':
                    _set_mouse(not _mouse_locked)

            # 이벤트가 없으면 select 타임아웃으로 대기 — 시그널 요청을
            # 최대 0.2초 안에 처리하기 위해 무한 블로킹을 피한다
            if not _display.pending_events():
                select.select([_display.fileno()], [], [], 0.2)
                if not _display.pending_events():
                    continue

            ev = _display.next_event()

            if ev.type not in (X.KeyPress, X.KeyRelease):
                continue

            ks = _display.keycode_to_keysym(ev.detail, 0)

            if ev.type == X.KeyPress:
                if _kb_locked:
                    if ks in (XK.XK_Control_L, XK.XK_Control_R):
                        _ctrl = True
                    elif ks in (XK.XK_Alt_L, XK.XK_Alt_R):
                        _alt = True
                    elif _ctrl and _alt and ks == XK.XK_q:
                        subprocess.run(['pkill', '-f', 'youtubekids.com'], check=False)
                        _cleanup_exit()
                        return
                    elif _ctrl and _alt and ks == XK.XK_k:
                        _set_kb(False)
                    elif _ctrl and _alt and ks == XK.XK_m:
                        _set_mouse(not _mouse_locked)
                    elif _ctrl and _alt and ks == XK.XK_s:
                        _skip_video()
                    elif _ctrl and _alt and ks == XK.XK_comma:
                        _vol('-5%')
                    elif _ctrl and _alt and ks == XK.XK_period:
                        _vol('+5%')
                    elif _ctrl and _alt and ks == XK.XK_slash:
                        _mute()
                    elif ks == _VOL_RAISE:
                        _vol('+5%')
                    elif ks == _VOL_LOWER:
                        _vol('-5%')
                    elif ks == _VOL_MUTE:
                        subprocess.Popen(
                            ['pactl', 'set-sink-mute', '@DEFAULT_SINK@', 'toggle'],
                            close_fds=True)
                    elif ks == XK.XK_p:
                        ts   = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                        dl   = os.path.expanduser('~/Downloads')
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
                        _forward_key(ev.detail)
                else:
                    # 키보드 잠금 해제 상태 — XGrabKey 이벤트만 도착
                    if ks == XK.XK_q:
                        subprocess.run(['pkill', '-f', 'youtubekids.com'], check=False)
                        _cleanup_exit()
                        return
                    elif ks == XK.XK_k:
                        _set_kb(True)
                    elif ks == XK.XK_m:
                        _set_mouse(not _mouse_locked)
                    elif ks == XK.XK_s:
                        _skip_video()
                    elif ks == XK.XK_comma:
                        _vol('-5%')
                    elif ks == XK.XK_period:
                        _vol('+5%')
                    elif ks == XK.XK_slash:
                        _mute()

            elif ev.type == X.KeyRelease and _kb_locked:
                if ks in (XK.XK_Control_L, XK.XK_Control_R):
                    _ctrl = False
                elif ks in (XK.XK_Alt_L, XK.XK_Alt_R):
                    _alt = False

    except Exception:
        traceback.print_exc()  # 원인 추적용 (kiosk 로그로 감)
    finally:
        _cleanup()


if __name__ == '__main__':
    main()
