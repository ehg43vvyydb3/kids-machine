#!/usr/bin/env bash
# 키즈머신 시작 스크립트
# 1) 터치패드(xinput) 비활성화  2) Firefox 키오스크로 YouTube Kids 실행
# kids 계정 로그인 시 autostart로 자동 실행된다.

set -uo pipefail

LOG="${HOME}/kids-kiosk.log"
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T') start-kids 시작 ==="

# 잠글 입력장치 이름 패턴 (대소문자 무시).
# 기본은 터치패드만. 무선 마우스(2.4G Mouse)까지 잠그려면:
#   LOCK_PATTERN='touchpad|2.4g mouse'
LOCK_PATTERN='touchpad'

# X 입력장치가 준비될 때까지 최대 10초 대기 (autostart 경쟁 상태 방지)
for _ in $(seq 1 10); do
    if xinput list --name-only >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# 장치명 자동 감지 후 비활성화 (id가 아닌 이름 기반 → 부팅마다 안전)
mapfile -t DEVICES < <(xinput list --name-only 2>/dev/null | grep -iE "$LOCK_PATTERN" || true)
if [ "${#DEVICES[@]}" -gt 0 ]; then
    for dev in "${DEVICES[@]}"; do
        # 잠금 실패해도 키오스크는 떠야 하므로 || 로 방어
        xinput disable "$dev" && echo "비활성화: $dev" || echo "비활성화 실패: $dev"
    done
else
    echo "경고: '$LOCK_PATTERN' 에 해당하는 장치 없음. 현재 장치 목록:"
    xinput list --name-only
fi

# Firefox 키오스크 (전용 프로필 → 세션복원/업데이트 팝업 차단)
PROFILE_DIR="${HOME}/.mozilla/kids-kiosk"
mkdir -p "$PROFILE_DIR"

echo "Firefox 키오스크 실행"
exec firefox --kiosk --profile "$PROFILE_DIR" "https://www.youtube.com/kids"
