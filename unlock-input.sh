#!/usr/bin/env bash
# 잠금 해제 스크립트 (부모 계정용)
# 사용법: Ctrl+Alt+F3 → 부모 계정 로그인 → ./unlock-input.sh → Ctrl+Alt+F1(또는 F7)로 복귀
# kids 세션의 터치패드 입력을 다시 활성화한다.

set -uo pipefail

KIDS_USER="kids"
LOCK_PATTERN='touchpad'   # start-kids.sh 와 동일하게 맞출 것

# 다른 유저(kids) 세션 제어를 위해 root 권한 필요
if [ "$(id -u)" -ne 0 ]; then
    echo "root 권한이 필요합니다. sudo로 재실행..."
    exec sudo "$0" "$@"
fi

# kids 세션 프로세스에서 DISPLAY / XAUTHORITY 환경값 추출
PID="$(pgrep -u "$KIDS_USER" xfce4-session | head -n1)"
[ -z "$PID" ] && PID="$(pgrep -u "$KIDS_USER" -f firefox | head -n1)"
if [ -z "$PID" ]; then
    echo "오류: kids 세션 프로세스를 찾지 못했습니다. kids가 로그인된 상태인지 확인하세요."
    exit 1
fi

ENVIRON="/proc/$PID/environ"
DISP="$(tr '\0' '\n' < "$ENVIRON" | grep -m1 '^DISPLAY=' | cut -d= -f2-)"
XAUTH="$(tr '\0' '\n' < "$ENVIRON" | grep -m1 '^XAUTHORITY=' | cut -d= -f2-)"
DISP="${DISP:-:0}"

# kids 사용자 권한 + 해당 세션 환경으로 xinput 실행
run_xinput() {
    sudo -u "$KIDS_USER" env DISPLAY="$DISP" ${XAUTH:+XAUTHORITY="$XAUTH"} xinput "$@"
}

mapfile -t DEVICES < <(run_xinput list --name-only 2>/dev/null | grep -iE "$LOCK_PATTERN" || true)
if [ "${#DEVICES[@]}" -eq 0 ]; then
    echo "오류: '$LOCK_PATTERN' 장치를 찾지 못했습니다."
    exit 1
fi

for dev in "${DEVICES[@]}"; do
    run_xinput enable "$dev" && echo "복원 완료: $dev" || echo "복원 실패: $dev"
done
echo "완료. Ctrl+Alt+F1 (또는 F7) 로 kids 세션 화면으로 돌아가세요."
