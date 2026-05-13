#!/usr/bin/env bash
# campound_dryrun.sh — Run strip_commercials dry-run on a completed Campound
# recording and send a macOS notification so you can review before cutting.
#
# Usage:
#   ./campound_dryrun.sh ~/Dropbox/TheTicket/2026-05-13_1900_Campound_2026_Night_1.aac

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="${1:-}"
LOG="$SCRIPT_DIR/logs/campound_dryrun.log"
PYTHON="$(command -v python3)"

mkdir -p "$SCRIPT_DIR/logs"

notify() {
    local subtitle="$1" msg="$2"
    osascript -e "display notification \"$msg\" with title \"Campound 2026\" subtitle \"$subtitle\" sound name \"Funk\"" 2>/dev/null || true
}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$LOG"; }

if [[ -z "$FILE" || ! -f "$FILE" ]]; then
    log "ERROR: file not found: ${FILE:-'(none provided)'}"
    notify "Error" "File not found: $(basename "${FILE:-unknown}")"
    exit 1
fi

BASENAME=$(basename "$FILE")
log "════════════════════════════════════════════════"
log "Campound dry-run: $BASENAME"

# Wait up to 5 minutes for ffmpeg to finish writing
for i in $(seq 1 10); do
    if ! lsof "$FILE" 2>/dev/null | grep -q ffmpeg; then
        break
    fi
    log "  File still open (attempt $i/10) — waiting 30s…"
    sleep 30
done

if lsof "$FILE" 2>/dev/null | grep -q ffmpeg; then
    log "  WARNING: file may still be recording — proceeding anyway"
fi

SIZE_MB=$(( $(stat -f "%z" "$FILE") / 1048576 ))
log "  File: $SIZE_MB MB — running dry-run (threshold=0, clustering only)"

"$PYTHON" "$SCRIPT_DIR/strip_commercials.py" "$FILE" \
    --dry-run --threshold 0 >> "$LOG" 2>&1
RC=$?

if [[ $RC -eq 0 ]]; then
    # Pull the summary line from the log
    SUMMARY=$(grep -E "Content|Commercial" "$LOG" | tail -2 | tr '\n' '  ' | sed 's/  *INFO  */ /g')
    log "  Done. $SUMMARY"
    notify "Night dry-run complete" "$BASENAME — review $LOG before stripping"
else
    log "  ERROR: dry-run failed (exit $RC)"
    notify "Dry-run failed" "Check $LOG"
fi

log "════════════════════════════════════════════════"
