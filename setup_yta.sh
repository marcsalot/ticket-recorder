#!/usr/bin/env zsh
# setup_yta.sh — Install (or repair) the `yta` YouTube audio recorder function
#               in ~/.zshrc, avoiding the zsh "no matches found" glob error
#               that occurs when passing unquoted YouTube URLs to a plain alias.
#
# Safe to run multiple times — idempotent.
#
# Usage:
#   ./setup_yta.sh
#   ./setup_yta.sh --dry-run   # show what would change without writing anything

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RECORDER="$SCRIPT_DIR/youtube_recorder.py"
ZSHRC="$HOME/.zshrc"
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔  $*${NC}"; }
info() { echo -e "${YELLOW}  →  $*${NC}"; }
err()  { echo -e "${RED}  ✘  $*${NC}"; }

echo ""
echo "yta setup — YouTube Audio Recorder"
echo "────────────────────────────────────"

# ── Verify recorder script exists ─────────────────────────────────────────────
if [[ ! -f "$RECORDER" ]]; then
    err "youtube_recorder.py not found at: $RECORDER"
    err "Run this script from the ticket-recorder project directory."
    exit 1
fi
ok "Recorder found: $RECORDER"

# ── Check for yt-dlp and ffmpeg ───────────────────────────────────────────────
missing=()
command -v yt-dlp &>/dev/null || missing+=(yt-dlp)
command -v ffmpeg  &>/dev/null || missing+=(ffmpeg)
if (( ${#missing[@]} > 0 )); then
    err "Missing required tool(s): ${missing[*]}"
    echo "       Install with:  brew install ${missing[*]}"
    echo "       Re-run this script after installing."
    exit 1
fi
ok "Dependencies present: yt-dlp, ffmpeg"

# ── Build the config block ────────────────────────────────────────────────────
# Two-part fix for zsh "no matches found" on unquoted YouTube URLs:
#   1. NO_NOMATCH  — pass unmatched glob patterns through literally
#   2. noglob precommand in the alias — suppresses glob expansion at call site
BLOCK=$(cat <<ENDBLOCK

# Don't error on unmatched globs — pass them through literally
setopt NO_NOMATCH

# YouTube Audio Recorder — yta
# noglob precommand prevents zsh expanding ? & = in YouTube URLs
unset -f yta 2>/dev/null
unalias yta 2>/dev/null
alias yta='noglob python3 ${RECORDER}'
ENDBLOCK
)

# ── Check current state of ~/.zshrc ───────────────────────────────────────────
STALE_ALIAS=false
FUNCTION_OK=false

if [[ -f "$ZSHRC" ]]; then
    grep -q "alias yta='noglob " "$ZSHRC" && STALE_ALIAS=false || true
    # Stale: old function-based definition
    grep -q "yta()" "$ZSHRC" && STALE_ALIAS=true
    # Check both required pieces are present
    grep -q "NO_NOMATCH" "$ZSHRC" && grep -q "alias yta='noglob " "$ZSHRC" && FUNCTION_OK=true
fi

# ── Report / apply ────────────────────────────────────────────────────────────
if $FUNCTION_OK && ! $STALE_ALIAS; then
    ok "yta function already correctly installed in $ZSHRC"
    echo ""
    echo "Reload with:  source ~/.zshrc"
    exit 0
fi

if $STALE_ALIAS; then
    info "Removing stale yta function definition from $ZSHRC"
    if ! $DRY_RUN; then
        sed -i '' '/yta()/d' "$ZSHRC"
        sed -i '' '/alias yta=/d' "$ZSHRC"
        sed -i '' '/unalias yta/d' "$ZSHRC"
        sed -i '' '/unset -f yta/d' "$ZSHRC"
    fi
fi

if ! $FUNCTION_OK; then
    info "Appending yta function to $ZSHRC"
    if $DRY_RUN; then
        echo ""
        echo "Would append:"
        echo "$BLOCK"
    else
        echo "$BLOCK" >> "$ZSHRC"
        ok "yta function installed in $ZSHRC"
    fi
fi

# ── Final instructions ────────────────────────────────────────────────────────
if ! $DRY_RUN; then
    echo ""
    echo "Reload your shell to activate:"
    echo "  source ~/.zshrc"
    echo ""
    echo "Then use:"
    echo "  yta https://www.youtube.com/watch?v=...          # download"
    echo "  yta https://www.youtube.com/watch?v=... --dry-run  # preview"
fi
