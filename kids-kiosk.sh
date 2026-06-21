#!/bin/bash

TIMER_PIDFILE="/tmp/kids-kiosk-timer.pid"
GRAB_PIDFILE="/tmp/kids-kb-grabber.pid"
POINTER_IDFILE="/tmp/kids-pointer-ids.txt"
TIMER_FLAG="/tmp/kids-timer-ended"

# 활성(마스터에 연결된) slave pointer ID 목록.
# 주의: disable 된 장치는 xinput list 에서 [floating slave] 로 표시되어
# 여기서 안 잡힌다 — 복원은 반드시 POINTER_IDFILE 에 저장된 ID 로 해야 한다.
attached_pointer_ids() {
    xinput list | grep -v "XTEST\|master" | grep "slave.*pointer" | \
        grep -o 'id=[0-9]*' | cut -d= -f2
}

# 마우스 복원: 저장된 ID 파일 + 현재 연결된 장치 모두 enable
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

# ── 절전 / 화면잠금 차단 ───────────────────────────────────────────
# 키오스크 실행 동안 노트북 뚜껑을 닫아도 절전(suspend)·idle 자동절전·자동
# 화면잠금을 하지 않도록 막는다. 화면만 꺼지고 세션은 그대로 유지돼 다시 열면
# 끊김 없이 재생이 이어진다. (lid-close 까지 막으려면 logind 의
# LidSwitchIgnoreInhibited=no 가 필요 — install.sh 가 설정한다.)
# sleep infinity 가 inhibitor 락을 잡고 있다가 키오스크 종료 시 함께 죽으며
# 자동 해제된다.
INHIBIT_PID=""
if command -v systemd-inhibit >/dev/null; then
    systemd-inhibit --what=handle-lid-switch:sleep:idle \
        --who="kids-kiosk" --why="키오스크 시청 중" --mode=block \
        sleep infinity &
    INHIBIT_PID=$!
fi

# light-locker 의 잠금화면은 kb-grabber 의 키보드 grab 과 충돌해 "화면 잠금
# 실패" 메시지를 띄우고 검은 화면을 만든다 — 세션 동안 멈춰 둔다.
LIGHTLOCKER_WAS_RUNNING=0
if pgrep -x light-locker >/dev/null; then
    LIGHTLOCKER_WAS_RUNNING=1
    pkill -x light-locker
fi

# 정상/비정상 종료 모두에서 inhibitor 해제 + light-locker 복원 보장
_release_power_guards() {
    [ -n "$INHIBIT_PID" ] && kill "$INHIBIT_PID" 2>/dev/null
    if [ "$LIGHTLOCKER_WAS_RUNNING" = 1 ] && command -v light-locker >/dev/null; then
        setsid light-locker >/dev/null 2>&1 &
    fi
}
trap _release_power_guards EXIT

# 시간 입력 + 자동재생 여부
# 인자를 직접 받으면(kids-control.py 원격 시작) 다이얼로그를 생략한다.
if [ $# -ge 3 ]; then
    MINUTES="$1"
    AUTOPLAY="$2"   # True or False
    SATURATION="$3" # 0-100
else
    DIALOG=$(python3 /home/jjejje/kids-machine/kids-input-dialog.py) || exit 1
    MINUTES=$(echo "$DIALOG" | cut -d, -f1)
    AUTOPLAY=$(echo "$DIALOG" | cut -d, -f2)  # True or False
    SATURATION=$(echo "$DIALOG" | cut -d, -f3)  # 0-100
fi

# 세션 상태 파일 기록 (kids-control.py 가 읽음)
python3 -c "
import json, sys, time
mins, ap = int(sys.argv[1]), sys.argv[2] == 'True'
now = time.time()
json.dump({'start_ts': now, 'end_ts': now + mins*60, 'minutes': mins, 'autoplay': ap},
          open('/tmp/kids-kiosk-state.json', 'w'))
" "$MINUTES" "$AUTOPLAY"

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
user_pref("media.autoplay.default", 0);
user_pref("media.autoplay.blocking_policy", 0);
user_pref("full-screen-api.allow-trusted-requests-only", false);
user_pref("full-screen-api.warning.timeout", 0);
user_pref("full-screen-api.warning.delay", -1);
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
echo "$TIMERBAR_PID" > /tmp/kids-timerbar.pid

# Firefox 키오스크 실행 (VA-API 하드웨어 디코딩 + Marionette 자동화 활성화)
MOZ_X11_EGL=1 MOZ_DISABLE_RDD_SANDBOX=1 LIBVA_DRIVER_NAME=iHD \
    firefox --profile "$KIDS_PROFILE" --marionette --kiosk "https://www.youtubekids.com/?hl=ko" &
FF_PID=$!

# 자동재생: 화면 좌표 클릭 대신 Marionette로 DOM을 보고 영상을 골라 재생.
# 백그라운드로 띄워 두면 영상이 끝날 때마다 다음 영상을 골라 계속 넘긴다.
# (홈 로딩/썸네일 대기는 kids-autoplay.py 내부에서 처리, FF 종료 시 스스로 종료)
AUTOPLAY_PID=""
if [ "$AUTOPLAY" = "True" ]; then
    python3 /home/jjejje/kids-machine/kids-autoplay.py &
    AUTOPLAY_PID=$!
    echo "$AUTOPLAY_PID" > /tmp/kids-autoplay.pid
fi

# 키보드 그랩 시작
python3 /home/jjejje/kids-machine/kids-kb-grabber.py &
echo $! > "$GRAB_PIDFILE"

# Firefox 종료 대기
wait $FF_PID

# 자동재생 루프 종료(FF가 닫히면 스스로 끝나지만 안전하게 정리)
[ -n "$AUTOPLAY_PID" ] && kill "$AUTOPLAY_PID" 2>/dev/null

# 타이머를 가장 먼저 종료 (플래그 생성 전에 막아야 함)
kill "$(cat "$TIMER_PIDFILE" 2>/dev/null)" 2>/dev/null

# 타임리미트 바 종료 (adjust_time으로 PID가 갱신됐을 수 있으므로 pkill 사용)
pkill -f kids-timer-bar.py 2>/dev/null

# 키보드 그랩 종료
kill "$(cat "$GRAB_PIDFILE" 2>/dev/null)" 2>/dev/null
sleep 0.3

# 마우스 항상 복원 (kb-grabber 종료 타이밍 무관하게 직접 보장)
# kb-grabber 가 잠근 채 죽었으면 장치가 floating 이라 ID 파일로만 찾을 수 있다
restore_pointers

if [ -f "$TIMER_FLAG" ]; then
    rm -f "$TIMER_FLAG"

    # 마우스/터치패드 전체 비활성화 (ID 를 먼저 저장한 뒤 잠가야 복원 가능)
    attached_pointer_ids > "$POINTER_IDFILE"
    while read id; do
        xinput disable "$id" 2>/dev/null
    done < "$POINTER_IDFILE"

    # 종료 화면 (내부에서 키보드 그랩 + Ctrl+Alt+Q 대기)
    python3 /home/jjejje/kids-machine/kids-end-screen.py

    # 마우스 복원
    cleanup_pointer
fi

rm -f "$TIMER_PIDFILE" "$GRAB_PIDFILE" "$POINTER_IDFILE" \
      /tmp/kids-autoplay.pid /tmp/kids-timerbar.pid \
      /tmp/kids-kiosk-state.json /tmp/kids-grabber-state.json \
      /tmp/kids-autoplay-status.json /tmp/kids-autoplay-cmd
