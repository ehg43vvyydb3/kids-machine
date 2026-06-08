#!/bin/bash

TIMER_PIDFILE="/tmp/kids-kiosk-timer.pid"
GRAB_PIDFILE="/tmp/kids-kb-grabber.pid"
POINTER_IDFILE="/tmp/kids-pointer-ids.txt"
TIMER_FLAG="/tmp/kids-timer-ended"

# 1920x1080 기준 유튜브 키즈 썸네일 중심 좌표 (상단 두 줄 총 8개)
# 실제 레이아웃과 다를 경우 아래 배열만 조정
THUMB_X=(330 750 1170 1590)
THUMB_Y=(280 510)

autoplay_click() {
    local col=$(( RANDOM % ${#THUMB_X[@]} ))
    local row=$(( RANDOM % ${#THUMB_Y[@]} ))
    local x=${THUMB_X[$col]}
    local y=${THUMB_Y[$row]}
    xdotool mousemove --sync "$x" "$y"
    xdotool click 1
}

cleanup_pointer() {
    if [ -f "$POINTER_IDFILE" ]; then
        while read id; do
            xinput enable "$id" 2>/dev/null
        done < "$POINTER_IDFILE"
        rm -f "$POINTER_IDFILE"
    fi
}

# 시간 입력 + 자동재생 여부
DIALOG=$(python3 /home/jjejje/kids-machine/kids-input-dialog.py) || exit 1
MINUTES=$(echo "$DIALOG" | cut -d, -f1)
AUTOPLAY=$(echo "$DIALOG" | cut -d, -f2)  # True or False
SATURATION=$(echo "$DIALOG" | cut -d, -f3)  # 0-100

MAIN_PROFILE="$HOME/.mozilla/firefox/cfn7ue77.default-release-1780757897236"
KIDS_PROFILE="$HOME/.mozilla/kids-kiosk"
mkdir -p "$KIDS_PROFILE/chrome"

cat > "$KIDS_PROFILE/chrome/userContent.css" <<EOF
@-moz-document domain("youtubekids.com") {
    html {
        filter: saturate(${SATURATION}%) !important;
    }
}
EOF

cat > "$KIDS_PROFILE/user.js" <<'USEREOF'
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("media.ffmpeg.vaapi.enabled", true);
user_pref("media.hardware-video-decoding.enabled", true);
user_pref("media.hardware-video-decoding.force-enabled", true);
user_pref("gfx.webrender.all", true);
user_pref("media.rdd-vpx.enabled", false);
user_pref("media.av1.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
USEREOF

# 쿠키를 메인 프로파일에서 복사하지 않는다.
# YouTube Kids 로그인/부모-아이 설정 상태는 쿠키만으로 유지되지 않고
# storage/(localStorage/IndexedDB)에 저장되므로, 쿠키만 덮어쓰면 세션이
# 깨져 무한로딩에 빠진다. kids-kiosk 프로파일에서 최초 1회 로그인하면
# 이후 실행에서 상태가 그대로 유지된다. (최초 설정 방법: setup-kids-login.sh)

rm -f "$TIMER_FLAG"

# 타이머: 지정 시간 후 종료 화면으로 전환
(sleep $(( MINUTES * 60 )) && touch "$TIMER_FLAG" && pkill -f "youtubekids.com") &
echo $! > "$TIMER_PIDFILE"

# 타임리미트 바 (화면 상단)
python3 /home/jjejje/kids-machine/kids-timer-bar.py "$MINUTES" &
TIMERBAR_PID=$!

# Firefox 키오스크 실행 (VA-API 하드웨어 디코딩 활성화)
MOZ_X11_EGL=1 MOZ_DISABLE_RDD_SANDBOX=1 LIBVA_DRIVER_NAME=iHD \
    firefox --profile "$KIDS_PROFILE" --kiosk "https://www.youtubekids.com/?hl=ko" &
FF_PID=$!

# Firefox 로딩 대기: FF 창 타이틀에서 YouTube 감지 후 썸네일 렌더링 대기
if [ "$AUTOPLAY" = "True" ]; then
    for _ in $(seq 1 30); do
        sleep 1
        WID=$(xdotool search --pid "$FF_PID" --name "" 2>/dev/null | head -1)
        TITLE=""
        [ -n "$WID" ] && TITLE=$(xdotool getwindowname "$WID" 2>/dev/null || true)
        echo "$TITLE" | grep -qi "youtube" && break
    done
    sleep 4
    autoplay_click
fi

# 키보드 그랩 시작
python3 /home/jjejje/kids-machine/kids-kb-grabber.py &
echo $! > "$GRAB_PIDFILE"

# Firefox 종료 대기
wait $FF_PID

# 타이머를 가장 먼저 종료 (플래그 생성 전에 막아야 함)
kill "$(cat "$TIMER_PIDFILE" 2>/dev/null)" 2>/dev/null

# 타임리미트 바 종료
kill "$TIMERBAR_PID" 2>/dev/null

# 키보드 그랩 종료
kill "$(cat "$GRAB_PIDFILE" 2>/dev/null)" 2>/dev/null
sleep 0.3

if [ -f "$TIMER_FLAG" ]; then
    rm -f "$TIMER_FLAG"

    # 마우스/터치패드 전체 비활성화
    xinput list | grep -v "XTEST\|master" | grep "slave.*pointer" | \
      grep -o 'id=[0-9]*' | cut -d= -f2 > "$POINTER_IDFILE"
    while read id; do
        xinput disable "$id" 2>/dev/null
    done < "$POINTER_IDFILE"

    # 종료 화면 (내부에서 키보드 그랩 + Ctrl+Alt+K 대기)
    python3 /home/jjejje/kids-machine/kids-end-screen.py

    # 마우스 복원
    cleanup_pointer
fi

rm -f "$TIMER_PIDFILE" "$GRAB_PIDFILE"
