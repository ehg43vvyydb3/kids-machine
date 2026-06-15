# kids-machine

> A Linux **YouTube Kids kiosk** — locks a child into a full-screen, time-limited,
> auto-playing YouTube Kids session with the keyboard/mouse safely guarded.
>
> 리눅스용 **YouTube Kids 키오스크** — 아이를 전체화면·시간제한·자동재생
> 세션에 가두고 키보드/마우스를 안전하게 잠급니다.

[English](#english) · [한국어](#한국어)

![platform](https://img.shields.io/badge/platform-Linux%20%2F%20X11-blue)
![firefox](https://img.shields.io/badge/Firefox-Marionette-orange)
![python](https://img.shields.io/badge/Python-3-green)

---

## English

### What it does

A set of shell + Python scripts that turn a Linux machine (tested on Xfce / X11,
1920×1080, Firefox 151) into a locked-down YouTube Kids player:

- **Full-screen kiosk** — Firefox `--kiosk` on a dedicated profile, so there's no
  address bar, tabs, or escape hatch.
- **Persistent login** — the kiosk reuses its own Firefox profile, so the Google
  login and parent/child setup survive restarts (set up once, never again).
- **Watch-time limit** — pick the minutes up front; a top-screen progress bar
  counts down and an "all done" end screen appears when time is up.
- **Reliable autoplay & auto-advance** — a pure-stdlib Firefox **Marionette**
  client picks a random video and, when it ends, automatically moves to the next
  one. No fragile pixel-clicking.
- **Hardware video decoding** — VA-API (Intel iHD) enabled, AV1 disabled to fall
  back to VP9 for smooth playback.
- **Color saturation control** — optionally desaturate the screen (a gentle nudge
  for screen-time).
- **Input lock** — keyboard and mouse are locked during playback (volume keys
  still work). Parent hotkeys: **Ctrl+Alt+Q** quits the kiosk, **Ctrl+Alt+K**
  toggles the keyboard lock, **Ctrl+Alt+M** toggles the mouse lock,
  **Ctrl+Alt+S** skips to the next video, **Ctrl+Alt+, / . / /** lower / raise /
  mute the volume. The same actions are available remotely from
  `kids-control.py`, which uses the matching `s` / `k` / `m` / `,` `.` `/` keys.

### Requirements

- Linux with **X11** (Xfce tested) — Wayland is not supported (relies on `xinput`,
  python-Xlib keyboard grab).
- **Firefox** (Marionette is built in; no extra driver needed).
- **Python 3** with `tkinter` and **python-Xlib** (`pip install python-xlib`).
- CLI tools: `xinput`, `pactl` (PulseAudio/PipeWire), `xfce4-screenshooter`
  (for the screenshot hotkey).

> ⚠️ Several scripts hard-code the path `/home/jjejje/kids-machine` and a specific
> Firefox profile name. Adjust these to your own paths before use.

### Quick start (interactive launcher)

```bash
# 1) One-time: log in to Google + finish the parent/child setup
#    inside the kiosk's own Firefox profile.
./setup-kids-login.sh

# 2) Launch the kiosk. A dialog asks for minutes / autoplay / saturation,
#    then YouTube Kids opens full-screen.
./kids-kiosk.sh
```

To stop early: **Ctrl+Alt+Q**. Toggle locks while watching: **Ctrl+Alt+K**
(keyboard) / **Ctrl+Alt+M** (mouse). Skip a video: **Ctrl+Alt+S**. Volume:
**Ctrl+Alt+,** down / **Ctrl+Alt+.** up / **Ctrl+Alt+/** mute.

### Unattended setup (dedicated `kids` account, auto-start on login)

```bash
# Creates a 'kids' user, installs start-kids.sh, and registers an
# autostart .desktop entry so the kiosk launches the moment kids logs in.
sudo bash install.sh
```

To re-enable a locked touchpad/mouse from the parent account:

```bash
sudo ./unlock-input.sh   # switch TTY: Ctrl+Alt+F3 → log in → run → Ctrl+Alt+F1
```

### Remote control (SSH)

`kids-control.py` is a terminal UI you can run from any machine that has SSH
access to the kiosk host.

```bash
ssh user@kiosk-host python3 /home/jjejje/kids-machine/kids-control.py
```

The first invocation starts a **tmux** session named `kids-control`.
Every subsequent call (from any SSH session) **re-attaches** to that same
session — there is never more than one control instance running.

```
─────────────────  Kids Kiosk Control  ─────────────── 09:41:23
  ● RUNNING  (Firefox PID 12345)
  Marionette ✓  자동재생 ON (PID 12399)
  키보드 잠금 🔒   마우스 잠금 🔒

  세션 남은 시간: 18분 44초

  영상 ID: abc123XYZ
  ▶  재생 중
  [████████░░░░░░░░░░░░░░░░░░░░] 03:12 / 11:05  (29%)
  예약 풀: 47개

─────────────────────────────────────────────────────
  [s] 다음 영상   [p] 일시정지/재생   [f] 전체화면   [=] +5분   [-] -5분
  [k] 키보드 잠금   [m] 마우스 잠금   [,] 음량-   [.] 음량+   [/] 음소거
  [Q] 키오스크 종료   [r] 새로고침   [q] UI 종료
```

**Key bindings:**

| Key | Action | Available when |
|-----|---------|---------------|
| `s` | Skip to next video | Autoplay running |
| `p` | Pause / resume | Autoplay running |
| `f` | Re-enter fullscreen | Autoplay running |
| `=` / `+` | Add 5 minutes | Kiosk running |
| `-` | Subtract 5 minutes | Kiosk running |
| `k` | Toggle keyboard lock | Kiosk running |
| `m` | Toggle mouse lock | Kiosk running |
| `,` / `.` | Volume down / up | Kiosk running |
| `/` | Toggle mute | Kiosk running |
| `Q` | Kill the kiosk (with confirmation) | Kiosk running |
| `o` | Start kiosk (opens setup form) | Kiosk stopped |
| `e` | Extend time + restart (end screen) | End screen showing |
| `d` | Dismiss end screen | End screen showing |
| `r` | Refresh status | Always |
| `q` | Quit the control UI | Always |

**End-screen control:** when time is up the kiosk shows an "all done" screen
with the keyboard still grabbed. `kids-control` detects this state and shows
`[e]` / `[d]` options. `e` asks for new parameters, then sends SIGTERM to the
end screen (which cleanly releases the keyboard grab) and starts a fresh kiosk.

### How autoplay works

`kids-autoplay.py` is a minimal, dependency-free Marionette client (TCP `2828`).
It pierces the Shadow DOM to collect visible `/watch?v=` thumbnails, picks one at
random, and navigates to it. It then **stays alive in a loop**: when the `<video>`
ends it advances to the next pick, replenishing its pool from the watch page's
suggestions and avoiding the last 15 videos. A child's pause is respected (it
won't force-resume); a stalled/failed load is skipped after 40 s. When Firefox
closes, the Marionette connection drops and the loop exits cleanly.

### File overview

| File | Role |
|------|------|
| `kids-kiosk.sh` | Main interactive launcher (dialog → timer → kiosk → lock → end screen). |
| `kids-autoplay.py` | Marionette autoplay loop: pick a video, auto-advance when it ends. |
| `kids-control.py` | SSH remote control TUI — monitor, skip, pause, adjust time, start/stop kiosk, dismiss end screen. |
| `setup-kids-login.sh` | One-time Google login + parent/child setup in the kiosk profile. |
| `kids-input-dialog.py` | Tk dialog: minutes / autoplay checkbox / saturation slider. |
| `kids-timer-bar.py` | Always-on-top top-screen countdown bar. |
| `kids-end-screen.py` | "Time's up" screen; stays grabbed, closes only on Ctrl+Alt+Q or remote SIGTERM. |
| `kids-kb-grabber.py` | Keyboard/mouse lock; volume keys, screenshot ('p'), Ctrl+Alt+Q/K/M/S + volume (`,`/`.`/`/`) hotkeys, SIGUSR1/2 remote toggles. |
| `kids-kiosk-exit.sh` | Timer-expiry hook: flag + quit Firefox. |
| `install.sh` | Creates the `kids` account and registers autostart. |
| `start-kids.sh` | Autostart entry for the `kids` account (disable touchpad → kiosk). |
| `kids-kiosk.desktop` | XDG autostart entry pointing at `start-kids.sh`. |
| `unlock-input.sh` | Parent tool to re-enable the locked touchpad/mouse. |

### Notes & limitations

- **X11 only.** The input-locking relies on `xinput` and python-Xlib.
- The infinite-loading bug (the loading star + progress bar repeating forever) was
  caused by copying cookies between profiles — **do not** overwrite the kiosk
  profile's cookies; YouTube Kids keeps its app state in `storage/`
  (localStorage/IndexedDB), not cookies. Use `setup-kids-login.sh` instead.

---

## 한국어

### 무엇인가요

리눅스 컴퓨터(Xfce / X11, 1920×1080, Firefox 151에서 검증)를 잠긴 YouTube Kids
재생기로 만드는 셸 + 파이썬 스크립트 모음입니다.

- **전체화면 키오스크** — 전용 프로필로 Firefox `--kiosk` 실행. 주소창·탭·탈출구가
  없습니다.
- **로그인 유지** — 키오스크가 자기 전용 Firefox 프로필을 그대로 재사용하므로
  구글 로그인과 부모/아이 설정이 재시작 후에도 유지됩니다(최초 1회만 설정).
- **시청 시간 제한** — 시작할 때 분을 입력하면 상단 진행바가 카운트다운하고, 시간이
  다 되면 종료 화면이 뜹니다.
- **확실한 자동재생 & 다음 영상 연속재생** — 순수 표준 라이브러리로 만든 Firefox
  **Marionette** 클라이언트가 무작위 영상을 골라 재생하고, 끝나면 자동으로 다음
  영상으로 넘어갑니다. 깨지기 쉬운 픽셀 클릭 방식이 아닙니다.
- **하드웨어 영상 디코딩** — VA-API(Intel iHD) 활성화, AV1은 끄고 VP9로 폴백해
  부드럽게 재생합니다.
- **채도 조절** — 화면 채도를 낮출 수 있습니다(시청 자제용).
- **입력 잠금** — 재생 중 키보드/마우스 잠금(볼륨키는 동작). 부모용 단축키:
  **Ctrl+Alt+Q** 키오스크 종료, **Ctrl+Alt+S** 다음 영상,
  **Ctrl+Alt+, / . / /** 음량 내림/올림/음소거,
  **Ctrl+Alt+K** 키보드 잠금 토글, **Ctrl+Alt+M**
  마우스 잠금 토글. 같은 토글을 `kids-control.py`에서 원격으로도 할 수 있습니다.

### 준비물

- **X11** 리눅스(Xfce 검증) — Wayland 미지원(`xinput`, python-Xlib 키보드 그랩에
  의존).
- **Firefox**(Marionette 내장, 별도 드라이버 불필요).
- **Python 3** + `tkinter` + **python-Xlib**(`pip install python-xlib`).
- CLI 도구: `xinput`, `pactl`(PulseAudio/PipeWire), `xfce4-screenshooter`
  (스크린샷 단축키용).

> ⚠️ 일부 스크립트는 `/home/jjejje/kids-machine` 경로와 특정 Firefox 프로필 이름이
> 하드코딩되어 있습니다. 사용 전 본인 환경에 맞게 수정하세요.

### 빠른 시작 (대화형 실행기)

```bash
# 1) 최초 1회: 키오스크 전용 Firefox 프로필 안에서
#    구글 로그인 + 부모/아이 설정을 끝냅니다.
./setup-kids-login.sh

# 2) 키오스크 실행. 시간/자동재생/채도를 묻는 다이얼로그가 뜨고,
#    이후 YouTube Kids가 전체화면으로 열립니다.
./kids-kiosk.sh
```

중간에 끄려면: **Ctrl+Alt+Q**. 시청 중 잠금 토글: **Ctrl+Alt+K**(키보드) /
**Ctrl+Alt+M**(마우스). 다음 영상: **Ctrl+Alt+S**. 음량:
**Ctrl+Alt+,** 내림 / **Ctrl+Alt+.** 올림 / **Ctrl+Alt+/** 음소거.

### 무인 설정 (전용 `kids` 계정, 로그인 시 자동 실행)

```bash
# 'kids' 사용자를 만들고 start-kids.sh를 설치한 뒤,
# kids 로그인 즉시 키오스크가 뜨도록 autostart .desktop을 등록합니다.
sudo bash install.sh
```

부모 계정에서 잠긴 터치패드/마우스를 다시 켜려면:

```bash
sudo ./unlock-input.sh   # TTY 전환: Ctrl+Alt+F3 → 로그인 → 실행 → Ctrl+Alt+F1
```

### 원격 제어 (SSH)

`kids-control.py`는 키오스크 호스트에 SSH로 접속한 어느 머신에서도 실행할 수 있는
터미널 UI입니다.

```bash
ssh user@kiosk-host python3 /home/jjejje/kids-machine/kids-control.py
```

최초 실행 시 `kids-control` 이름의 **tmux** 세션을 만들고, 이후 실행 시에는
**기존 세션에 재접속**합니다 — 인스턴스는 항상 하나만 존재합니다.

**키 바인딩:**

| 키 | 동작 | 사용 가능 상태 |
|----|------|--------------|
| `s` | 다음 영상으로 건너뜀 | 자동재생 실행 중 |
| `p` | 일시정지 / 재생 | 자동재생 실행 중 |
| `f` | 전체화면 재진입 | 자동재생 실행 중 |
| `=` / `+` | +5분 | 키오스크 실행 중 |
| `-` | −5분 | 키오스크 실행 중 |
| `k` | 키보드 잠금 토글 | 키오스크 실행 중 |
| `m` | 마우스 잠금 토글 | 키오스크 실행 중 |
| `,` / `.` | 음량 내림 / 올림 | 키오스크 실행 중 |
| `/` | 음소거 토글 | 키오스크 실행 중 |
| `Q` | 키오스크 종료 (확인 필요) | 키오스크 실행 중 |
| `o` | 키오스크 시작 (설정 폼 입력) | 키오스크 정지 상태 |
| `e` | 시간 연장 + 재시작 | 종료화면 표시 중 |
| `d` | 종료화면 닫기 | 종료화면 표시 중 |
| `r` | 상태 새로고침 | 항상 |
| `q` | 제어 UI 종료 | 항상 |

**종료화면 제어:** 시간이 다 되면 키보드 그랩이 유지된 채 종료화면이 뜹니다.
`kids-control`이 이 상태를 감지해 `[e]` / `[d]` 키를 표시합니다.
`e`는 새 파라미터를 입력받은 뒤 종료화면에 SIGTERM을 보내(키보드 그랩 해제 후 닫힘)
새 키오스크를 시작합니다.

### 자동재생 동작 방식

`kids-autoplay.py`는 외부 의존성 없는 최소 Marionette 클라이언트(TCP `2828`)입니다.
섀도우 DOM까지 뚫고 보이는 `/watch?v=` 썸네일을 모아 무작위로 하나를 골라 이동합니다.
그 뒤 **루프로 살아 있으면서**: `<video>`가 끝나면 다음 영상으로 넘어가고, watch
페이지의 추천에서 풀을 보충하며 최근 15개는 피합니다. 아이가 일시정지하면 존중하고
(강제로 다시 틀지 않음), 로딩 실패로 40초간 멈추면 건너뜁니다. Firefox가 닫히면
Marionette 연결이 끊겨 루프가 깔끔하게 종료됩니다.

### 파일 구성

| 파일 | 역할 |
|------|------|
| `kids-kiosk.sh` | 메인 대화형 실행기(다이얼로그 → 타이머 → 키오스크 → 잠금 → 종료화면). |
| `kids-autoplay.py` | Marionette 자동재생 루프: 영상 선택, 끝나면 다음 영상 자동 전환. |
| `kids-control.py` | SSH 원격 제어 TUI — 모니터링, 건너뜀, 일시정지, 시간 조정, 시작/종료, 종료화면 제어. |
| `setup-kids-login.sh` | 키오스크 프로필에서 최초 1회 구글 로그인 + 부모/아이 설정. |
| `kids-input-dialog.py` | Tk 다이얼로그: 시간 / 자동재생 체크 / 채도 슬라이더. |
| `kids-timer-bar.py` | 항상 위에 뜨는 상단 카운트다운 바. |
| `kids-end-screen.py` | "시청 종료" 화면; 그랩 유지, Ctrl+Alt+Q 또는 원격 SIGTERM으로 닫힘. |
| `kids-kb-grabber.py` | 키보드/마우스 잠금; 볼륨키, 스크린샷('p'), Ctrl+Alt+Q/K/M/S + 음량(`,`/`.`/`/`) 단축키, SIGUSR1/2 원격 토글. |
| `kids-kiosk-exit.sh` | 타이머 만료 훅: 플래그 + Firefox 종료. |
| `install.sh` | `kids` 계정 생성 및 autostart 등록. |
| `start-kids.sh` | `kids` 계정 autostart 진입점(터치패드 끄고 → 키오스크). |
| `kids-kiosk.desktop` | `start-kids.sh`를 가리키는 XDG autostart 항목. |
| `unlock-input.sh` | 잠긴 터치패드/마우스를 다시 켜는 부모용 도구. |

### 참고 & 한계

- **X11 전용.** 입력 잠금이 `xinput`과 python-Xlib에 의존합니다.
- 무한로딩 버그(로딩 별 + 진행바가 영원히 반복)는 프로필 간 쿠키 복사가 원인이었습니다.
  키오스크 프로필의 쿠키를 **덮어쓰지 마세요** — YouTube Kids는 앱 상태를 쿠키가 아닌
  `storage/`(localStorage/IndexedDB)에 저장합니다. 대신 `setup-kids-login.sh`를
  사용하세요.
