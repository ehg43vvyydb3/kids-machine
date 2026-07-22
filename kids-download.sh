#!/bin/bash
# 오프라인 키오스크용 영상 다운로드 헬퍼.
# 부모가 미리 "승인한" 유튜브(등) 영상을 로컬 라이브러리(~/kids-videos)에
# yt-dlp 로 받아둔다. 여기 받아둔 것만 kids-offline.sh 가 재생한다.
#
# 사용법:
#   kids-download.sh <URL> [<URL> ...]        # 개별 영상/재생목록 URL
#   kids-download.sh -f urls.txt              # 한 줄에 하나씩 URL 목록 파일
# 환경변수:
#   KIDS_VIDEO_DIR  다운로드 폴더 (기본 ~/kids-videos)

set -uo pipefail

VIDEO_DIR="${KIDS_VIDEO_DIR:-$HOME/kids-videos}"

# yt-dlp 는 YouTube 서명 챌린지(n-param)를 풀 JS 런타임이 있어야 실제 다운로드
# URL 이 403 이 안 난다. userspace deno 를 PATH 에 얹어 자동 감지되게 한다.
# (설치: deno-x86_64-unknown-linux-gnu.zip 를 ~/.deno/bin 에 풀기)
[ -d "$HOME/.deno/bin" ] && export PATH="$HOME/.deno/bin:$PATH"

# yt-dlp 선택: 배포판 apt 판은 금방 낡아 YouTube 에 막히므로(iOS/android
# player API 거부) ~/.local/bin 의 최신 standalone 바이너리를 우선 사용한다.
# 최신본 설치: curl -fSL -o ~/.local/bin/yt-dlp \
#   https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp && chmod +x ~/.local/bin/yt-dlp
if [ -x "$HOME/.local/bin/yt-dlp" ]; then
    YTDLP="$HOME/.local/bin/yt-dlp"
elif command -v yt-dlp >/dev/null; then
    YTDLP="$(command -v yt-dlp)"
else
    echo "오류: yt-dlp 가 없습니다. ~/.local/bin 에 최신본을 받으세요(위 주석 참고)." >&2
    exit 1
fi

# URL 수집 (직접 인자 또는 -f 파일)
URLS=()
if [ "${1:-}" = "-f" ]; then
    [ -n "${2:-}" ] || { echo "사용법: kids-download.sh -f urls.txt" >&2; exit 1; }
    while IFS= read -r line; do
        line="${line%%#*}"                       # 주석 제거
        line="$(echo "$line" | tr -d '[:space:]')"
        [ -n "$line" ] && URLS+=("$line")
    done < "$2"
else
    URLS=("$@")
fi

if [ "${#URLS[@]}" -eq 0 ]; then
    echo "사용법: kids-download.sh <URL> [<URL> ...]" >&2
    echo "        kids-download.sh -f urls.txt" >&2
    exit 1
fi

mkdir -p "$VIDEO_DIR"

# 포맷 선택은 ffmpeg 유무에 따라 갈린다:
#  - ffmpeg 있음: 영상/음성 분리 스트림을 mp4 로 병합 → 최대 1080p 고화질.
#  - ffmpeg 없음: 병합이 불가하므로 오디오까지 든 "통합(progressive)" 단일
#    파일만 고른다(대개 720p, 없으면 360p 포맷 18). 그래야 무음 파일이 안 생김.
# mpv 재생 자체엔 ffmpeg 가 필요 없다(내장 디코더 사용). 병합에만 쓰인다.
if command -v ffmpeg >/dev/null; then
    FORMAT='bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b'
else
    echo "안내: ffmpeg 없음 → 통합 포맷(대개 720p)으로 받습니다." >&2
    echo "      고화질을 원하면 sudo apt install ffmpeg 후 다시 받으세요." >&2
    FORMAT='b[height<=720][ext=mp4]/b[height<=720]/b[ext=mp4]/18/b'
fi

# 파일명은 아스키 안전하게 정리, 이미 받은 건 건너뜀(--download-archive).
"$YTDLP" \
    --no-playlist-reverse \
    --format "$FORMAT" \
    --merge-output-format mp4 \
    --download-archive "$VIDEO_DIR/.downloaded.txt" \
    --output "$VIDEO_DIR/%(title).80s [%(id)s].%(ext)s" \
    "${URLS[@]}"

echo
echo "완료. 현재 라이브러리($VIDEO_DIR):"
find "$VIDEO_DIR" -maxdepth 1 -type f \
    \( -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.webm' \) \
    -printf '  %f\n' | sort
