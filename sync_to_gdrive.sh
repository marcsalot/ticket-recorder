#!/bin/bash
# sync_to_gdrive.sh — Sync ~/TheTicket to Google Drive
# Run manually or via launchd for automatic background sync.
#
# Usage:
#   ./sync_to_gdrive.sh          # sync local -> Google Drive
#   ./sync_to_gdrive.sh --pull   # sync Google Drive -> local (download)
#   ./sync_to_gdrive.sh --dry-run # preview only

GDRIVE_REMOTE="gdrive:The Ticket"
LOCAL_DIR="$HOME/TheTicket"
LOG="$HOME/ticket-recorder/logs/gdrive_sync.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p "$(dirname "$LOG")"

if [[ "$1" == "--pull" ]]; then
    echo "[$TIMESTAMP] Pulling from Google Drive -> $LOCAL_DIR" | tee -a "$LOG"
    rclone sync "$GDRIVE_REMOTE" "$LOCAL_DIR" \
        --progress \
        --log-file="$LOG" \
        --log-level INFO \
        "${@:2}"
elif [[ "$1" == "--dry-run" ]]; then
    echo "[$TIMESTAMP] Dry run: $LOCAL_DIR -> Google Drive" | tee -a "$LOG"
    rclone sync "$LOCAL_DIR" "$GDRIVE_REMOTE" \
        --dry-run \
        --progress \
        "${@:2}"
else
    echo "[$TIMESTAMP] Pushing $LOCAL_DIR -> Google Drive" | tee -a "$LOG"
    rclone sync "$LOCAL_DIR" "$GDRIVE_REMOTE" \
        --progress \
        --log-file="$LOG" \
        --log-level INFO \
        "$@"
fi

echo "[$TIMESTAMP] Sync complete" | tee -a "$LOG"
