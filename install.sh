#!/usr/bin/env bash
# 키즈머신 설치 스크립트 (부모 계정에서 sudo로 1회 실행)
#   실행:  sudo bash /home/jjejje/kids-machine/install.sh
set -euo pipefail

SRC="/home/jjejje/kids-machine"
KIDS_HOME="/home/kids"

if [ "$(id -u)" -ne 0 ]; then
    echo "root 권한 필요: sudo bash $0"
    exit 1
fi

# 1) kids 계정 (없으면 생성, sudo 그룹에 넣지 않음)
if id kids >/dev/null 2>&1; then
    echo "[1/4] kids 계정 이미 존재 — 건너뜀"
else
    echo "[1/4] kids 계정 생성"
    useradd -m -s /bin/bash kids
    echo "  → 비밀번호 설정(자동로그인 안 쓸 경우):  sudo passwd kids"
fi

# 2) start-kids.sh 설치
echo "[2/4] start-kids.sh 설치"
install -o kids -g kids -m 755 "$SRC/start-kids.sh" "$KIDS_HOME/start-kids.sh"

# 3) autostart 등록
echo "[3/4] autostart 등록"
install -d -o kids -g kids -m 755 "$KIDS_HOME/.config/autostart"
install -o kids -g kids -m 644 "$SRC/kids-kiosk.desktop" \
    "$KIDS_HOME/.config/autostart/kids-kiosk.desktop"

# 4) logind: 뚜껑 닫힘 절전이 inhibitor 락을 존중하도록 설정.
#    기본값(LidSwitchIgnoreInhibited=yes)은 뚜껑을 닫으면 inhibitor 를 무시하고
#    절전한다. no 로 바꿔야 kids-kiosk.sh 의 systemd-inhibit 가 절전을 막는다.
echo "[4/4] logind 절전 inhibitor 설정"
install -d -m 755 /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/kids-kiosk.conf <<'EOF'
[Login]
LidSwitchIgnoreInhibited=no
EOF
echo "  → 적용하려면 재부팅하세요 (logind 재시작은 그래픽 세션을 끊을 수 있음)."

echo
echo "설치 완료."
echo "  - $KIDS_HOME/start-kids.sh"
echo "  - $KIDS_HOME/.config/autostart/kids-kiosk.desktop"
echo "  - /etc/systemd/logind.conf.d/kids-kiosk.conf"
echo "  - /home/jjejje/unlock-input.sh (이미 설치됨)"
echo
echo "다음: 재부팅 후 kids 계정으로 로그인하면 키오스크가 자동 실행됩니다."
