#!/usr/bin/env bash
# process_recordings.sh — Post-process completed KTCK recordings.
#
# Scans the output directory for .aac files that are:
#   1. A completed show recording (YYYY-MM-DD_HHMM_ShowName.aac pattern)
#   2. Not currently being written by ffmpeg  (lsof check)
#   3. Not already processed                  (no _clean.aac counterpart)
#
# For each qualifying file, runs strip_commercials.py to remove ads, then
# sends a macOS notification when the batch is done.
#
# Run manually, or install as a 2-hourly background job:
#   ./deploy.sh schedule-processor --output-dir ~/Dropbox/TheTicket
#
# Usage:
#   ./process_recordings.sh
#   ./process_recordings.sh --output-dir ~/Dropbox/TheTicket
#   ./process_recordings.sh --dry-run          # preview, no changes
#   ./process_recordings.sh --since 2026-05-12 # only files from this date
#   ./process_recordings.sh --threshold 0      # clustering only, no RMS gate

set -uo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/Dropbox/TheTicket}"
LOG_FILE="$SCRIPT_DIR/logs/processor.log"
STALE_SECS=120           # file must be idle this long before we touch it
PYTHON="$(command -v python3)"

# ── Argument parsing ──────────────────────────────────────────────────────────
DRY_RUN=false
SINCE=""
STRIP_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)   OUTPUT_DIR="$2";  shift 2 ;;
        --dry-run)      DRY_RUN=true;     shift   ;;
        --since)        SINCE="$2";       shift 2 ;;
        --threshold|--min-break|--max-break|--silence-db|--max-spot|--max-intra-gap)
                        STRIP_ARGS+=("$1" "$2");  shift 2 ;;
        *)              shift ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S')  $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

notify() {
    local subtitle="$1" message="$2"
    if [[ "$(uname)" == "Darwin" ]] && command -v osascript &>/dev/null; then
        osascript -e \
            "display notification \"$message\" with title \"KTCK Processor\" subtitle \"$subtitle\" sound name \"Glass\""
    fi
}

file_is_active() {
    # Returns 0 (true) if ffmpeg currently has this file open
    lsof "$1" 2>/dev/null | grep -q "ffmpeg"
}

file_age_secs() {
    # Seconds since the file was last modified
    if [[ "$(uname)" == "Darwin" ]]; then
        echo $(( $(date +%s) - $(stat -f %m "$1") ))
    else
        echo $(( $(date +%s) - $(stat -c %Y "$1") ))
    fi
}

file_mb() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo $(( $(stat -f "%z" "$1") / 1048576 ))
    else
        echo $(( $(stat -c %s "$1") / 1048576 ))
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
log "════════════════════════════════════════════════"
log "Processing pass — output dir: $OUTPUT_DIR"
[[ "$DRY_RUN" == "true" ]] && log "DRY RUN — no files will be modified"
[[ -n "$SINCE"           ]] && log "Filter: files on or after $SINCE"

if [[ ! -d "$OUTPUT_DIR" ]]; then
    log "ERROR: output directory not found: $OUTPUT_DIR"
    exit 1
fi

processed=0
skipped=0
errors=0

for file in "$OUTPUT_DIR"/*.aac; do
    [[ -f "$file" ]] || continue

    basename=$(basename "$file")

    # ── Skip _clean files and non-show files ──────────────────────────────────
    [[ "$basename" == *_clean.aac ]] && continue
    [[ "$basename" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}_.*\.aac$ ]] \
        || { log "  SKIP (not a show file): $basename"; ((skipped++)); continue; }

    # ── Optional date filter ──────────────────────────────────────────────────
    if [[ -n "$SINCE" ]]; then
        file_date="${basename:0:10}"          # YYYY-MM-DD
        [[ "$file_date" < "$SINCE" ]] \
            && { log "  SKIP (before --since $SINCE): $basename"; ((skipped++)); continue; }
    fi

    # ── Skip if already processed ─────────────────────────────────────────────
    clean="${file%.aac}_clean.aac"
    if [[ -f "$clean" ]]; then
        log "  SKIP (already processed): $basename"
        ((skipped++))
        continue
    fi

    # ── Skip if ffmpeg still has the file open ────────────────────────────────
    if file_is_active "$file"; then
        log "  SKIP (recording in progress): $basename"
        ((skipped++))
        continue
    fi

    # ── Skip if modified too recently (race-condition buffer) ─────────────────
    age=$(file_age_secs "$file")
    if (( age < STALE_SECS )); then
        log "  SKIP (${age}s old, waiting for ${STALE_SECS}s idle): $basename"
        ((skipped++))
        continue
    fi

    # ── Process ───────────────────────────────────────────────────────────────
    mb=$(file_mb "$file")
    log "  PROCESSING: $basename  (${mb} MB, ${age}s idle)"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  DRY RUN: strip_commercials.py \"$basename\" ${STRIP_ARGS[*]:-}"
        ((processed++))
        continue
    fi

    if "$PYTHON" "$SCRIPT_DIR/strip_commercials.py" "$file" ${STRIP_ARGS[@]+"${STRIP_ARGS[@]}"} \
            >> "$LOG_FILE" 2>&1; then

        if [[ -f "$clean" ]]; then
            clean_mb=$(file_mb "$clean")
            pct=$(( 100 - 100 * clean_mb / (mb > 0 ? mb : 1) ))
            log "  ✔  Done: ${clean_mb} MB  (removed ~${pct}%)"
            ((processed++))
        else
            log "  ✔  Done (clean file not created — check strip_commercials output)"
            ((processed++))
        fi
    else
        log "  ✘  Error: strip_commercials.py failed for $basename"
        ((errors++))
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
log "────────────────────────────────────────────────"
log "Done: ${processed} processed  ${skipped} skipped  ${errors} errors"
log "════════════════════════════════════════════════"

if (( processed > 0 )) && [[ "$DRY_RUN" == "false" ]]; then
    notify "Processing complete" "${processed} recording(s) processed"
fi

if (( errors > 0 )); then
    notify "Processing errors" "${errors} file(s) failed — check logs"
    exit 1
fi
