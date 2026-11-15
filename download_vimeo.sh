#!/bin/bash
# Download Vimeo video 134371535 (Richard Norton - Flow Complex #5)
# Usage:
#   ./download_vimeo.sh                        # try via Chrome cookies
#   ./download_vimeo.sh [output_dir]           # specify output directory
#   ./download_vimeo.sh [output_dir] <cdn_url> # pass CDN URL from DevTools
#
# If Cloudflare blocks the request:
#   1. Open https://vimeo.com/134371535 in Chrome and play the video
#   2. DevTools -> Network -> filter "Media" or ".m3u8"
#   3. Copy the CDN URL and pass it as the second argument

OUTPUT_DIR="${1:-$HOME/Downloads}"
CDN_URL="${2:-}"

if [[ -n "$CDN_URL" ]]; then
  URL="$CDN_URL"
  echo "Using provided CDN URL."
else
  URL="https://vimeo.com/134371535"
  echo "Attempting via Chrome cookies (open the page in Chrome first if this fails)."
fi

echo "Downloading Vimeo video 134371535..."
echo "Output directory: $OUTPUT_DIR"

yt-dlp \
  --no-playlist \
  --output "$OUTPUT_DIR/%(title)s.%(ext)s" \
  --format "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" \
  --merge-output-format mp4 \
  --cookies-from-browser chrome \
  --write-info-json \
  "$URL"

if [ $? -eq 0 ]; then
  echo "Download complete."
else
  echo "Download failed. If the turnstile token has expired, you may need to refresh the URL."
  exit 1
fi
