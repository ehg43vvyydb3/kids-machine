#!/bin/bash
# 타이머 만료 시 호출 - flag 파일 생성 후 Firefox 종료
# (Ctrl+Alt+Q 비상탈출은 kids-kb-grabber.py 가 직접 처리)

touch /tmp/kids-timer-ended
pkill -f "youtubekids.com" 2>/dev/null
kill "$(cat /tmp/kids-kiosk-timer.pid 2>/dev/null)" 2>/dev/null
rm -f /tmp/kids-kiosk-timer.pid
