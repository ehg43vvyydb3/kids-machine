# CLAUDE.md

리눅스 YouTube Kids **키오스크**. 아이를 전체화면·시간제한·자동재생 세션에
가두고 키보드/마우스를 잠근다. 부모는 단축키나 `kids-control.py`로 원격 제어한다.

상세 사용법/원격 제어는 `README.md` 참고. 이 문서는 구조와 **함정(유의사항)** 위주.

## 구조 (실행 흐름)

`start-kids.sh` → `kids-kiosk.sh`(오케스트레이터)가 아래를 띄움:

- **Firefox** `--kiosk --marionette` (전용 프로파일 `~/.mozilla/kids-kiosk`)
- `kids-autoplay.py` — Marionette로 DOM을 보고 다음 영상 자동재생 (픽셀 클릭 아님)
- `kids-kb-grabber.py` — 키보드 grab + 마우스 xinput disable (시청 중 잠금)
- `kids-timer-bar.py` — 상단 남은시간 바
- 타이머 만료 시 `kids-end-screen.py` — 종료 화면(키보드 grab 유지)

`kids-control.py` — 부모용 curses TUI. 시그널/명령파일로 위 프로세스들을 제어.

### 오프라인 변형 (인터넷 없이)

`kids-offline.sh` — 위 오케스트레이터의 **병렬본**. Firefox+YouTube Kids+
`kids-autoplay.py` 대신 **mpv** 로 로컬 영상(`~/kids-videos`, `KIDS_VIDEO_DIR`)을
전체화면·시간제한·플레이리스트 자동재생으로 튼다. 잠금/타이머바/종료화면/절전차단은
온라인용 스크립트를 **그대로 재사용**한다. 영상은 `kids-download.sh`(yt-dlp)로
부모가 미리 받아둔다. mpv 필요(`sudo apt install mpv`), 다운로드 의존성은 함정 8 참고.
mpv 제어는 IPC 유닉스 소켓 `/tmp/kids-mpv.sock`(skip=playlist-next 등).

### 프로세스 간 통신 (모두 `/tmp` 파일)
- `kids-grabber-state.json` — grabber가 현재 잠금 상태 export
- `kids-autoplay-cmd` — autoplay에 보내는 명령(skip/pause/resume/fullscreen)
- `kids-autoplay-status.json`, `kids-kiosk-state.json` — 상태 공유
- `kids-pointer-ids.txt` — 잠근 포인터 장치 ID (복원용)
- `*.pid` 파일들 — 각 프로세스 PID
- grabber 토글: `SIGUSR1`=키보드, `SIGUSR2`=마우스

## 함정 (유의사항)

### 0. 공유 스크립트의 재생엔진 결합부는 env 로만 분기한다 (온라인/오프라인)
`kids-kb-grabber.py`·`kids-timer-bar.py`는 온라인/오프라인이 **공유**한다.
재생엔진에 묶인 두 지점을 env 로 파라미터화했으니 **기본값(온라인)을 깨지 말 것**:
- `KIDS_KILL_PATTERN` (기본 `youtubekids.com`) — 종료(Q)·시간재조정 시 `pkill -f` 대상.
  오프라인은 `kids-mpv.sock`(mpv argv 에 든 소켓 경로) 로 설정.
- `KIDS_MPV_SOCK` — 설정되면 skip 을 파일명령 대신 mpv IPC(`playlist-next`)로 보냄.
env 없이 실행하면 온라인 동작 그대로. 이 두 스크립트를 고칠 때 하드코딩으로
되돌리지 말 것(오프라인이 깨진다).

### 1. 키보드 grab과 화면잠금(light-locker)은 충돌한다
grabber/end-screen은 X 키보드를 **독점 grab**한다. 동시에 `light-locker` 같은
화면잠금 데몬이 잠금화면을 띄우려 키보드를 grab하면 **충돌 → "화면 잠금 실패"
메시지 + 검은 화면**. → `kids-kiosk.sh`가 실행 동안 light-locker를 멈추고
종료 시 `trap`으로 복원한다. 새 잠금 데몬을 도입하면 같은 문제 재발 주의.

### 2. suspend/resume 하면 X grab이 풀린다
노트북 뚜껑 닫힘 등으로 절전했다 깨어나면 X 서버가 **모든 grab을 해제**한다.
grabber는 `_kb_locked=True`인데 실제 grab은 없는 상태가 되어 잠금이 풀린 것처럼
동작한다. 마우스도 포인터 장치가 **새 ID로 재열거**되며 살아난다.
- 1차 방어: `kids-kiosk.sh`의 `systemd-inhibit`가 절전/idle/lid-close를 차단
  (화면만 꺼지고 세션 유지 → 재생 끊김 없음).
- 2차 방어(보험): `kids-kb-grabber.py`가 2초마다 `_reassert_locks()`로 grab
  재획득 + 살아난 포인터 재disable. 배터리 위급 절전처럼 inhibitor로 못 막는
  경우 대비. (`kids-end-screen.py`에는 원래 heartbeat 재grab이 있었음.)

### 3. lid inhibitor는 logind 설정이 있어야 먹힌다 ★
logind 기본값 `LidSwitchIgnoreInhibited=yes`는 **뚜껑 닫힘 시 inhibitor를 무시**
하고 절전한다. `systemd-inhibit --what=handle-lid-switch`만으로는 부족하고,
`/etc/systemd/logind.conf.d/kids-kiosk.conf`에 `LidSwitchIgnoreInhibited=no`가
**반드시** 있어야 한다. `install.sh`가 설치하며, 적용은 **재부팅** 필요
(logind 재시작은 그래픽 세션을 끊을 수 있음).

### 4. 마우스 잠금/복원은 ID 파일로만 가능
xinput `disable`된 장치는 `xinput list`에서 `[floating slave]`로 표시되어
일반 조회로는 안 잡힌다. 잠그기 **전에** ID를 `kids-pointer-ids.txt`에 저장해야
나중에 복원할 수 있다. grabber가 잠근 채 죽어도 `kids-kiosk.sh`의
`restore_pointers`가 이 파일로 복원한다.

### 5. 쿠키를 메인 프로파일에서 복사하지 말 것
YouTube Kids 로그인 상태는 쿠키가 아니라 storage(localStorage/IndexedDB)에
있다. 쿠키만 덮어쓰면 세션이 깨져 **무한로딩**. kids-kiosk 프로파일에서
최초 1회 로그인(`setup-kids-login.sh`)하면 이후 유지된다.

### 6. 자동재생은 Marionette DOM 제어
픽셀 좌표 클릭이 아니라 Marionette로 DOM을 읽어 영상을 고른다. 좌표/레이아웃
변경에 흔들리지 않지만, YouTube Kids DOM 구조가 바뀌면
`kids-autoplay.py`의 셀렉터/JS를 갱신해야 한다.

### 7. `/tmp`는 재부팅하면 비워진다 — 하루 넘게 살아야 하는 데이터는 두지 말 것
위 프로세스 간 통신 파일들은 전부 `/tmp`에 두는데, 이는 `systemd-tmpfiles-setup.service`가
부팅 시 `--remove` 옵션으로 `/tmp`를 정리하기 때문에 **키오스크 세션이 어차피 재부팅과
함께 끝나는 상태**(pid, grabber 상태 등)에는 적합하다. 반면 일일 누적 시청량처럼
**같은 날 안에서 재부팅을 넘겨서도 유지돼야 하는 값**을 `/tmp`에 두면 재부팅 시 0으로
리셋되는 버그가 난다(실제로 겪음). 그런 값은 `/home/jjejje/.kids-daily-watch.json`처럼
`/tmp` 밖의 홈 디렉터리에 저장한다.

### 8. 오프라인 다운로드는 최신 yt-dlp + deno + ffmpeg 3종이 필요하다 ★
`kids-download.sh`(yt-dlp)로 YouTube 영상을 받으려면:
- **최신 yt-dlp**: 배포판 apt 판(예: 2024.04)은 금세 낡아 iOS/android player API 가
  거부돼 추출 자체가 막힌다. standalone 최신 바이너리를 `~/.local/bin/yt-dlp` 에 둔다
  (release 의 `yt-dlp` 파일 받아 chmod +x). 스크립트가 이 경로를 우선 사용.
- **deno (JS 런타임)**: 없으면 추출은 되지만 다운로드 URL 이 **403 Forbidden**.
  YouTube n-param 서명 챌린지를 풀 JS 런타임이 필요하다. userspace 로 설치 가능
  (`~/.deno/bin`, sudo 불필요). 스크립트가 있으면 PATH 에 얹어 자동 감지.
- **ffmpeg**: 없으면 영상/음성 분리 스트림을 병합 못해 통합(progressive) 단일
  포맷(대개 720p)만 받는다(무음 파일 방지 위해 스크립트가 자동으로 그렇게 폴백).
  있으면 최대 1080p 병합. mpv **재생** 자체엔 ffmpeg 불필요(내장 디코더).
- mpv 옵션은 채도 조절이 `--saturate` 가 아니라 **`--saturation`**(-100..0, 0=원본).

## 검증
- 셸: `bash -n kids-kiosk.sh`, `bash -n install.sh`
- 파이썬: `python3 -m py_compile kids-kb-grabber.py` (다른 .py도 동일)
- 환경: X11 세션 필요(Wayland 아님). grabber는 `python3-xlib`, autoplay는
  Firefox Marionette 의존.
