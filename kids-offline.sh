#!/bin/bash
# 오프라인 키오스크 오케스트레이터 (온라인용 kids-kiosk.sh 의 병렬본).
#
# 인터넷 없이, 부모가 미리 받아둔 로컬 영상을 mpv 로 전체화면·시간제한·
# 자동재생(플레이리스트) 세션으로 재생한다. 키보드/마우스 잠금·타이머 바·
# 종료 화면·절전차단은 온라인용과 동일한 스크립트를 그대로 재사용하되,
# 재생 엔진 결합부만 env(KIDS_KILL_PATTERN / KIDS_MPV_SOCK)로 mpv 를 가리킨다.
#
# 영상 라이브러리: 기본 ~/kids-videos (kids-download.sh 로 yt-dlp 다운로드).
# 사용법:
#   kids-offline.sh                      # 다이얼로그로 시간/자동재생/채도 입력
#   kids-offline.sh <분> <True|False> <0-100>   # 원격 시작(다이얼로그 생략)

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VIDEO_DIR="${KIDS_VIDEO_DIR:-$HOME/kids-videos}"
MPV_SOCK="/tmp/kids-mpv.sock"
PLAYLIST="/tmp/kids-offline-playlist.txt"

TIMER_PIDFILE="/tmp/kids-kiosk-timer.pid"
GRAB_PIDFILE="/tmp/kids-kb-grabber.pid"
POINTER_IDFILE="/tmp/kids-pointer-ids.txt"
TIMER_FLAG="/tmp/kids-timer-ended"

# 재생 엔진 결합부: 재사용하는 grabber/timer-bar 가 이 값으로 mpv 를 제어.
# mpv 의 argv 에 소켓 경로가 들어가므로 'kids-mpv.sock' 이 유일한 pkill 패턴.
export KIDS_KILL_PATTERN="kids-mpv.sock"
export KIDS_MPV_SOCK="$MPV_SOCK"

# 실행창(시간/자동재생/채도 다이얼로그) 제목에 오프라인임을 표시
export KIDS_DIALOG_TITLE="유튜브 키즈 - 오프라인"

# ── 마우스 잠금/복원 (online 과 동일 로직) ─────────────────────────
attached_pointer_ids() {
    xinput list | grep -v "XTEST\|master" | grep "slave.*pointer" | \
        grep -o 'id=[0-9]*' | cut -d= -f2
}
restore_pointers() {
    { cat "$POINTER_IDFILE" 2>/dev/null; attached_pointer_ids; } | \
      sort -u | while read id; do
        [ -n "$id" ] && xinput enable "$id" 2>/dev/null
    done
}
cleanup_pointer() {
    restore_pointers
    rm -f "$POINTER_IDFILE"
}

# ── 절전 / 화면잠금 차단 (online 과 동일) ──────────────────────────
INHIBIT_PID=""
if command -v systemd-inhibit >/dev/null; then
    systemd-inhibit --what=handle-lid-switch:sleep:idle \
        --who="kids-offline" --why="오프라인 시청 중" --mode=block \
        sleep infinity &
    INHIBIT_PID=$!
fi

LIGHTLOCKER_WAS_RUNNING=0
if pgrep -x light-locker >/dev/null; then
    LIGHTLOCKER_WAS_RUNNING=1
    pkill -x light-locker
fi

_release_power_guards() {
    [ -n "$INHIBIT_PID" ] && kill "$INHIBIT_PID" 2>/dev/null
    if [ "$LIGHTLOCKER_WAS_RUNNING" = 1 ] && command -v light-locker >/dev/null; then
        setsid light-locker >/dev/null 2>&1 &
    fi
}
trap _release_power_guards EXIT

# ── 사전 점검 ─────────────────────────────────────────────────────
if ! command -v mpv >/dev/null; then
    echo "오류: mpv 가 없습니다.  sudo apt install mpv 로 설치하세요." >&2
    exit 1
fi

mkdir -p "$VIDEO_DIR"
find "$VIDEO_DIR" -maxdepth 2 -type f \
    \( -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.webm' \
       -o -iname '*.m4v' -o -iname '*.avi' -o -iname '*.mov' \) \
    | sort > "$PLAYLIST"
if [ ! -s "$PLAYLIST" ]; then
    echo "오류: $VIDEO_DIR 에 재생할 영상이 없습니다." >&2
    echo "      kids-download.sh '<유튜브 URL>' 로 먼저 받아두세요." >&2
    exit 1
fi

# ── 시간/자동재생/채도 입력 ────────────────────────────────────────
if [ $# -ge 3 ]; then
    MINUTES="$1"
    AUTOPLAY="$2"   # True(플레이리스트 무한반복) or False(1회 재생 후 종료)
    SATURATION="$3" # 0-100 (CSS saturate% 와 동일 의미)
else
    DIALOG=$(python3 "$HERE/kids-input-dialog.py") || exit 1
    MINUTES=$(echo "$DIALOG" | cut -d, -f1)
    AUTOPLAY=$(echo "$DIALOG" | cut -d, -f2)
    SATURATION=$(echo "$DIALOG" | cut -d, -f3)
fi

# 세션 상태 파일 (kids-control.py / timer-bar 가 읽음)
python3 -c "
import json, sys, time
mins, ap = int(sys.argv[1]), sys.argv[2] == 'True'
now = time.time()
json.dump({'start_ts': now, 'end_ts': now + mins*60, 'minutes': mins, 'autoplay': ap},
          open('/tmp/kids-kiosk-state.json', 'w'))
" "$MINUTES" "$AUTOPLAY"

# CSS saturate%(0-100, 100=원본) → mpv --saturation(-100..0, 0=원본)
MPV_SAT=$(( SATURATION - 100 ))
[ "$MPV_SAT" -lt -100 ] && MPV_SAT=-100
[ "$MPV_SAT" -gt 0 ] && MPV_SAT=0

LOOP_ARG="--loop-playlist=no"
[ "$AUTOPLAY" = "True" ] && LOOP_ARG="--loop-playlist=inf"

rm -f "$TIMER_FLAG" "$MPV_SOCK"

# ── 타이머: 만료 시 mpv 종료 → 종료 화면으로 전환 ──────────────────
(sleep $(( MINUTES * 60 )) && touch "$TIMER_FLAG" && pkill -f "$KIDS_KILL_PATTERN") &
echo $! > "$TIMER_PIDFILE"

# ── 상단 타임리미트 바 ────────────────────────────────────────────
python3 "$HERE/kids-timer-bar.py" "$MINUTES" &
echo $! > /tmp/kids-timerbar.pid

# ── mpv 재생 (IPC 소켓으로 skip/pause 제어) ───────────────────────
# 키보드는 grabber 가 전역 grab 하므로 mpv 는 키 입력을 받지 않는다.
# --no-input-* 는 혹시 새는 입력에 대한 방어. 마우스 커서 숨김.
mpv --fullscreen \
    --input-ipc-server="$MPV_SOCK" \
    --no-osc --osd-level=0 --cursor-autohide=always --no-input-cursor \
    --no-input-default-bindings --input-conf=/dev/null \
    --idle=no --keep-open=no --shuffle \
    $LOOP_ARG \
    --saturation="$MPV_SAT" \
    --playlist="$PLAYLIST" \
    >/dev/null 2>&1 &
MPV_PID=$!

# ── 키보드 그랩 시작 ──────────────────────────────────────────────
python3 "$HERE/kids-kb-grabber.py" &
echo $! > "$GRAB_PIDFILE"

# ── mpv 종료 대기 ─────────────────────────────────────────────────
wait $MPV_PID

# 정리: 타이머 먼저 종료(플래그 생성 전 차단), 그다음 바/그랩
kill "$(cat "$TIMER_PIDFILE" 2>/dev/null)" 2>/dev/null
pkill -f kids-timer-bar.py 2>/dev/null
kill "$(cat "$GRAB_PIDFILE" 2>/dev/null)" 2>/dev/null
sleep 0.3
restore_pointers

if [ -f "$TIMER_FLAG" ]; then
    rm -f "$TIMER_FLAG"

    # 마우스/터치패드 전체 비활성화 (ID 를 먼저 저장한 뒤 잠가야 복원 가능)
    attached_pointer_ids > "$POINTER_IDFILE"
    while read id; do
        xinput disable "$id" 2>/dev/null
    done < "$POINTER_IDFILE"

    # 종료 화면 (내부에서 키보드 그랩 + Ctrl+Alt+Q 대기)
    END_SCREEN_ARGS=""
    [ -f /tmp/kids-poweroff-enabled ] && END_SCREEN_ARGS="--poweroff"
    python3 "$HERE/kids-end-screen.py" $END_SCREEN_ARGS

    cleanup_pointer
fi

rm -f "$TIMER_PIDFILE" "$GRAB_PIDFILE" "$POINTER_IDFILE" \
      /tmp/kids-timerbar.pid "$MPV_SOCK" "$PLAYLIST" \
      /tmp/kids-kiosk-state.json /tmp/kids-grabber-state.json
