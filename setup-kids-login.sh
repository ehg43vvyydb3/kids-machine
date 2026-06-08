#!/bin/bash
# 최초 1회: kids-kiosk 프로파일에 직접 로그인 + 부모/아이 설정을 한다.
# 여기서 한 번 설정해두면 이후 kids-kiosk.sh 실행 때 상태가 유지된다.
#
# 사용법: ./setup-kids-login.sh
#   1) 일반 Firefox 창이 뜨면 구글 계정으로 로그인
#   2) YouTube Kids 부모 확인 + 아이 프로필 선택까지 완료
#   3) 창을 닫으면 끝. (저장은 자동)

KIDS_PROFILE="$HOME/.mozilla/kids-kiosk"
mkdir -p "$KIDS_PROFILE"

# 이전 키오스크 실행이 남긴 깨진 쿠키/락 정리 → 깨끗한 상태로 로그인
rm -f "$KIDS_PROFILE"/cookies.sqlite* "$KIDS_PROFILE/lock" "$KIDS_PROFILE/.parentlock"

echo "kids-kiosk 프로파일로 일반 창을 엽니다. 로그인 + 부모/아이 설정 후 창을 닫으세요."
firefox --new-instance --profile "$KIDS_PROFILE" "https://www.youtubekids.com/?hl=ko"
echo "설정 완료. 이제 키오스크를 실행하면 로그인 상태가 유지됩니다."
