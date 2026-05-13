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
RETENTION_DAYS="${RETENTION_DAYS:-30}"

SERVICE_NAME="com.theticket.recorder"          # macOS label / systemd unit name
CLEANUP_NAME="com.theticket.cleanup"
MONITOR_NAME="com.theticket.monitor"
PROCESSOR_NAME="com.theticket.processor"

MACOS_PLIST="$HOME/Library/LaunchAgents/${SERVICE_NAME}.plist"
MACOS_CLEANUP_PLIST="$HOME/Library/LaunchAgents/${CLEANUP_NAME}.plist"
MACOS_MONITOR_PLIST="$HOME/Library/LaunchAgents/${MONITOR_NAME}.plist"
MACOS_PROCESSOR_PLIST="$HOME/Library/LaunchAgents/${PROCESSOR_NAME}.plist"
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
            --output-dir) OUTPUT_DIR="$2";       shift 2 ;;
            --days)       RETENTION_DAYS="$2";   shift 2 ;;
            *)            shift ;;
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
        <!-- caffeinate -i prevents Mac idle sleep during recording -->
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
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

    <!-- Ensure Homebrew binaries (ffmpeg) are on PATH -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

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

# ── Cleanup scheduling — macOS launchd ───────────────────────────────────────
schedule_cleanup_macos() {
    [[ -f "$SCRIPT_DIR/cleanup.py" ]] || die "cleanup.py not found at $SCRIPT_DIR/cleanup.py"
    mkdir -p "$LOG_DIR" "$(dirname "$MACOS_CLEANUP_PLIST")"

    cat > "$MACOS_CLEANUP_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${CLEANUP_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${SCRIPT_DIR}/cleanup.py</string>
        <string>--output-dir</string>
        <string>${OUTPUT_DIR}</string>
        <string>--days</string>
        <string>${RETENTION_DAYS}</string>
        <string>--keep-special</string>
    </array>

    <!-- Run daily at 2:00 AM -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>   <integer>2</integer>
        <key>Minute</key> <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/cleanup.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/cleanup.error.log</string>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
</dict>
</plist>
PLIST

    launchctl unload "$MACOS_CLEANUP_PLIST" 2>/dev/null || true
    launchctl load -w "$MACOS_CLEANUP_PLIST"

    green "✔  Cleanup scheduled daily at 2:00 AM (retain ${RETENTION_DAYS} days, keep-special)"
    echo   "   Log:      $LOG_DIR/cleanup.log"
    echo   "   Dry run:  python3 $SCRIPT_DIR/cleanup.py --output-dir $OUTPUT_DIR --days $RETENTION_DAYS --keep-special --dry-run"
}

unschedule_cleanup_macos() {
    if [[ -f "$MACOS_CLEANUP_PLIST" ]]; then
        launchctl unload "$MACOS_CLEANUP_PLIST" 2>/dev/null || true
        rm -f "$MACOS_CLEANUP_PLIST"
        green "✔  Cleanup schedule removed"
    else
        yellow "No cleanup schedule found."
    fi
}

# ── Cleanup scheduling — Linux cron ──────────────────────────────────────────
schedule_cleanup_linux() {
    [[ -f "$SCRIPT_DIR/cleanup.py" ]] || die "cleanup.py not found at $SCRIPT_DIR/cleanup.py"
    local CRON_LINE="0 2 * * * ${PYTHON_BIN} ${SCRIPT_DIR}/cleanup.py --output-dir ${OUTPUT_DIR} --days ${RETENTION_DAYS} --keep-special >> ${LOG_DIR}/cleanup.log 2>&1"
    mkdir -p "$LOG_DIR"
    # Preserve existing crontab, remove stale cleanup entry, append new one
    (crontab -l 2>/dev/null | grep -v 'cleanup.py'; echo "$CRON_LINE") | crontab -

    green "✔  Cleanup scheduled daily at 2:00 AM (retain ${RETENTION_DAYS} days, keep-special)"
    echo   "   Log:      $LOG_DIR/cleanup.log"
    echo   "   Crontab:  crontab -l | grep cleanup"
    echo   "   Dry run:  python3 $SCRIPT_DIR/cleanup.py --output-dir $OUTPUT_DIR --days $RETENTION_DAYS --keep-special --dry-run"
}

unschedule_cleanup_linux() {
    if crontab -l 2>/dev/null | grep -q 'cleanup.py'; then
        crontab -l 2>/dev/null | grep -v 'cleanup.py' | crontab -
        green "✔  Cleanup cron job removed"
    else
        yellow "No cleanup cron job found."
    fi
}


# ── Health monitor scheduling — macOS launchd ─────────────────────────────────
schedule_monitor_macos() {
    [[ -f "$SCRIPT_DIR/monitor_health.py" ]] || die "monitor_health.py not found"
    mkdir -p "$LOG_DIR" "$(dirname "$MACOS_MONITOR_PLIST")"
    cat > "$MACOS_MONITOR_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${MONITOR_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${SCRIPT_DIR}/monitor_health.py</string>
        <string>--output-dir</string>
        <string>${OUTPUT_DIR}</string>
    </array>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/monitor.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/monitor.error.log</string>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
    launchctl unload "$MACOS_MONITOR_PLIST" 2>/dev/null || true
    launchctl load -w "$MACOS_MONITOR_PLIST"
    green "✔  Health monitor scheduled every 15 minutes"
    echo   "   Log:  $LOG_DIR/monitor.log"
    echo   "   Test: python3 $SCRIPT_DIR/monitor_health.py --test"
}

unschedule_monitor_macos() {
    if [[ -f "$MACOS_MONITOR_PLIST" ]]; then
        launchctl unload "$MACOS_MONITOR_PLIST" 2>/dev/null || true
        rm -f "$MACOS_MONITOR_PLIST"
        green "✔  Health monitor removed"
    else
        yellow "No health monitor found."
    fi
}

# ── Health monitor scheduling — Linux cron ────────────────────────────────────
schedule_monitor_linux() {
    [[ -f "$SCRIPT_DIR/monitor_health.py" ]] || die "monitor_health.py not found"
    local CRON_LINE="*/15 * * * * ${PYTHON_BIN} ${SCRIPT_DIR}/monitor_health.py --output-dir ${OUTPUT_DIR} >> ${LOG_DIR}/monitor.log 2>&1"
    mkdir -p "$LOG_DIR"
    (crontab -l 2>/dev/null | grep -v 'monitor_health.py'; echo "$CRON_LINE") | crontab -
    green "✔  Health monitor scheduled every 15 minutes"
    echo   "   Log:  $LOG_DIR/monitor.log"
    echo   "   Test: python3 $SCRIPT_DIR/monitor_health.py --test"
}

unschedule_monitor_linux() {
    if crontab -l 2>/dev/null | grep -q 'monitor_health.py'; then
        crontab -l 2>/dev/null | grep -v 'monitor_health.py' | crontab -
        green "✔  Health monitor removed"
    else
        yellow "No health monitor found."
    fi
}


# ── Processor scheduling — macOS launchd ─────────────────────────────────────
schedule_processor_macos() {
    [[ -f "$SCRIPT_DIR/process_recordings.sh" ]] || die "process_recordings.sh not found"
    mkdir -p "$LOG_DIR" "$(dirname "$MACOS_PROCESSOR_PLIST")"
    cat > "$MACOS_PROCESSOR_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PROCESSOR_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCRIPT_DIR}/process_recordings.sh</string>
        <string>--output-dir</string>
        <string>${OUTPUT_DIR}</string>
    </array>
    <key>StartInterval</key>
    <integer>7200</integer>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/processor.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/processor.error.log</string>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
    launchctl unload "$MACOS_PROCESSOR_PLIST" 2>/dev/null || true
    launchctl load -w "$MACOS_PROCESSOR_PLIST"
    green "✔  Processor scheduled every 2 hours"
    echo   "   Log:  $LOG_DIR/processor.log"
    echo   "   Test: bash $SCRIPT_DIR/process_recordings.sh --dry-run --output-dir $OUTPUT_DIR"
}

unschedule_processor_macos() {
    if [[ -f "$MACOS_PROCESSOR_PLIST" ]]; then
        launchctl unload "$MACOS_PROCESSOR_PLIST" 2>/dev/null || true
        rm -f "$MACOS_PROCESSOR_PLIST"
        green "✔  Processor removed"
    else
        yellow "No processor found."
    fi
}

# ── Processor scheduling — Linux cron ────────────────────────────────────────
schedule_processor_linux() {
    [[ -f "$SCRIPT_DIR/process_recordings.sh" ]] || die "process_recordings.sh not found"
    local CRON_LINE="0 */2 * * * /bin/bash ${SCRIPT_DIR}/process_recordings.sh --output-dir ${OUTPUT_DIR} >> ${LOG_DIR}/processor.log 2>&1"
    mkdir -p "$LOG_DIR"
    (crontab -l 2>/dev/null | grep -v 'process_recordings.sh'; echo "$CRON_LINE") | crontab -
    green "✔  Processor scheduled every 2 hours"
    echo   "   Log:  $LOG_DIR/processor.log"
    echo   "   Test: bash $SCRIPT_DIR/process_recordings.sh --dry-run --output-dir $OUTPUT_DIR"
}

unschedule_processor_linux() {
    if crontab -l 2>/dev/null | grep -q 'process_recordings.sh'; then
        crontab -l 2>/dev/null | grep -v 'process_recordings.sh' | crontab -
        green "✔  Processor removed"
    else
        yellow "No processor found."
    fi
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
    schedule-cleanup)
        check_prereqs
        "schedule_cleanup_${PLATFORM}"
        ;;
    unschedule-cleanup)
        "unschedule_cleanup_${PLATFORM}"
        ;;
    schedule-monitor)
        check_prereqs
        "schedule_monitor_${PLATFORM}"
        ;;
    unschedule-monitor)
        "unschedule_monitor_${PLATFORM}"
        ;;
    schedule-processor)
        check_prereqs
        "schedule_processor_${PLATFORM}"
        ;;
    unschedule-processor)
        "unschedule_processor_${PLATFORM}"
        ;;
    help|--help|-h|*)
        echo "Usage: $0 {install|uninstall|status|logs|schedule-cleanup|unschedule-cleanup} [options]"
        echo ""
        echo "  install              Install and start the recorder as a background service"
        echo "  uninstall            Stop and remove the service"
        echo "  status               Show service status"
        echo "  logs                 Tail the service log"
        echo "  schedule-cleanup     Install daily 2 AM cleanup job (launchd / cron)"
        echo "  unschedule-cleanup   Remove the cleanup job
  schedule-monitor     Install 15-min health check with macOS notifications
  unschedule-monitor   Remove the health monitor
  schedule-processor   Install 2-hourly commercial stripping processor
  unschedule-processor Remove the processor"
        echo ""
        echo "Options:"
        echo "  --output-dir DIR   Where to save recordings (default: ~/ticket_recordings)"
        echo "  --days N           Retention period for schedule-cleanup (default: 30)"
        echo ""
        echo "Examples:"
        echo "  $0 install"
        echo "  $0 install --output-dir ~/Dropbox/TheTicket"
        echo "  $0 schedule-cleanup --output-dir ~/Dropbox/TheTicket --days 30"
        echo "  $0 logs"
        ;;
esac
