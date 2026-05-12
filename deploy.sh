#!/usr/bin/env bash
# deploy.sh — Install or remove the KTCK recorder as a background service.
#
# macOS  → launchd user agent  (~/Library/LaunchAgents/)
# Linux  → systemd user unit   (~/.config/systemd/user/)
#
# Usage:
#   ./deploy.sh install [--output-dir /path/to/recordings]
#   ./deploy.sh uninstall
#   ./deploy.sh status
#   ./deploy.sh logs

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECORDER="$SCRIPT_DIR/recorder.py"
LOG_DIR="$SCRIPT_DIR/logs"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/ticket_recordings}"

SERVICE_NAME="com.theticket.recorder"          # macOS label / systemd unit name

MACOS_PLIST="$HOME/Library/LaunchAgents/${SERVICE_NAME}.plist"
LINUX_UNIT="$HOME/.config/systemd/user/${SERVICE_NAME}.service"

# ── Helpers ───────────────────────────────────────────────────────────────────
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
red()    { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
die()    { red "Error: $*"; exit 1; }

require() {
    command -v "$1" &>/dev/null || die "'$1' is not installed. $2"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
}

# ── Preflight checks ──────────────────────────────────────────────────────────
check_prereqs() {
    require python3 "Install via: brew install python (macOS) or apt install python3 (Linux)"
    require ffmpeg  "Install via: brew install ffmpeg (macOS) or apt install ffmpeg (Linux)"

    [[ -f "$RECORDER" ]] || die "recorder.py not found at $RECORDER"

    PYTHON_BIN="$(command -v python3)"
    PYTHON_VER="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[:2])')"
    # Require Python 3.9+ for zoneinfo
    "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' \
        || die "Python 3.9+ required (found $PYTHON_VER)"
}

# ── macOS launchd ─────────────────────────────────────────────────────────────
install_macos() {
    mkdir -p "$LOG_DIR" "$OUTPUT_DIR"
    mkdir -p "$(dirname "$MACOS_PLIST")"

    cat > "$MACOS_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${SERVICE_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>-u</string>
        <string>${RECORDER}</string>
        <string>--output-dir</string>
        <string>${OUTPUT_DIR}</string>
    </array>

    <!-- Start automatically at login and keep running -->
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <!-- Wait 10 s before restarting after a crash -->
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <!-- Redirect stdout/stderr to log files -->
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/recorder.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/recorder.error.log</string>

    <!-- Run in the project directory so overrides.json is found -->
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
</dict>
</plist>
PLIST

    # Unload any existing instance first (ignore error if not loaded)
    launchctl unload "$MACOS_PLIST" 2>/dev/null || true
    launchctl load -w "$MACOS_PLIST"

    green "✔  Installed and started: $SERVICE_NAME"
    echo   "   Recordings → $OUTPUT_DIR"
    echo   "   Logs       → $LOG_DIR/recorder.log"
    echo   ""
    echo   "   Status:  launchctl list | grep theticket"
    echo   "   Logs:    tail -f $LOG_DIR/recorder.log"
    echo   "   Stop:    launchctl unload $MACOS_PLIST"
}

uninstall_macos() {
    if [[ -f "$MACOS_PLIST" ]]; then
        launchctl unload "$MACOS_PLIST" 2>/dev/null || true
        rm -f "$MACOS_PLIST"
        green "✔  Removed $SERVICE_NAME"
    else
        yellow "Nothing to remove (plist not found)."
    fi
}

status_macos() {
    launchctl list | grep theticket || yellow "Service is not loaded."
}

logs_macos() {
    tail -n 50 -f "$LOG_DIR/recorder.log" 2>/dev/null \
        || yellow "Log file not found: $LOG_DIR/recorder.log"
}

# ── Linux systemd (user session) ──────────────────────────────────────────────
install_linux() {
    mkdir -p "$LOG_DIR" "$OUTPUT_DIR"
    mkdir -p "$(dirname "$LINUX_UNIT")"

    cat > "$LINUX_UNIT" <<UNIT
[Unit]
Description=KTCK The Ticket – Live Stream Recorder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${PYTHON_BIN} -u ${RECORDER} --output-dir ${OUTPUT_DIR}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
UNIT

    systemctl --user daemon-reload
    systemctl --user enable --now "${SERVICE_NAME}.service"

    green "✔  Installed and started: $SERVICE_NAME"
    echo   "   Recordings → $OUTPUT_DIR"
    echo   ""
    echo   "   Status:  systemctl --user status $SERVICE_NAME"
    echo   "   Logs:    journalctl --user -u $SERVICE_NAME -f"
    echo   "   Stop:    systemctl --user stop $SERVICE_NAME"

    # Enable lingering so the service survives logout
    if command -v loginctl &>/dev/null; then
        loginctl enable-linger "$USER" 2>/dev/null \
            && echo "   Linger:  enabled (service persists after logout)" \
            || yellow "   Note: run 'loginctl enable-linger $USER' as root to persist after logout"
    fi
}

uninstall_linux() {
    if systemctl --user list-unit-files "${SERVICE_NAME}.service" &>/dev/null; then
        systemctl --user disable --now "${SERVICE_NAME}.service" 2>/dev/null || true
        rm -f "$LINUX_UNIT"
        systemctl --user daemon-reload
        green "✔  Removed $SERVICE_NAME"
    else
        yellow "Nothing to remove (unit not found)."
    fi
}

status_linux() {
    systemctl --user status "${SERVICE_NAME}.service" || true
}

logs_linux() {
    journalctl --user -u "${SERVICE_NAME}.service" -n 50 -f
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
COMMAND="${1:-help}"
shift || true
parse_args "$@"

OS="$(uname)"
case "$OS" in
    Darwin) PLATFORM=macos ;;
    Linux)  PLATFORM=linux ;;
    *)      die "Unsupported platform: $OS" ;;
esac

case "$COMMAND" in
    install)
        check_prereqs
        "install_${PLATFORM}"
        ;;
    uninstall)
        "uninstall_${PLATFORM}"
        ;;
    status)
        "status_${PLATFORM}"
        ;;
    logs)
        "logs_${PLATFORM}"
        ;;
    help|--help|-h|*)
        echo "Usage: $0 {install|uninstall|status|logs} [--output-dir DIR]"
        echo ""
        echo "  install    Install and start the recorder as a background service"
        echo "  uninstall  Stop and remove the service"
        echo "  status     Show service status"
        echo "  logs       Tail the service log"
        echo ""
        echo "Options:"
        echo "  --output-dir DIR   Where to save recordings (default: ~/ticket_recordings)"
        echo ""
        echo "Examples:"
        echo "  $0 install"
        echo "  $0 install --output-dir ~/Dropbox/TheTicket"
        echo "  $0 logs"
        ;;
esac
