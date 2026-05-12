#!/usr/bin/env python3
"""
monitor_health.py — Periodic health check for the KTCK recorder.
Designed to run every 15 minutes via launchd (macOS) or cron (Linux).
Sends macOS notifications when something needs attention.

Checks:
  1. launchd service is running (PID non-zero, exit code 0)
  2. Output file is growing if currently in a recording window
  3. Available disk space on the output volume

Usage:
    python3 monitor_health.py                           # uses ~/Dropbox/TheTicket
    python3 monitor_health.py --output-dir ~/Documents/TheTicket
    python3 monitor_health.py --disk-warn-gb 30
    python3 monitor_health.py --test                    # force all alerts for testing
"""

import argparse
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────────────────

SERVICE_NAME    = "com.theticket.recorder"
DEFAULT_OUT_DIR = Path.home() / "Dropbox" / "TheTicket"
DISK_WARN_GB    = 50      # alert when free space drops below this
STALE_MINS      = 20      # alert if newest file unchanged for this long during recording window
CT              = ZoneInfo("America/Chicago")

# Recording windows (Central Time) — alerts only fire during these hours.
# Mirrors the record=True entries in recorder.py's SHOW_SCHEDULE.
RECORDING_WINDOWS = [
    # Weekdays (Mon=0 … Fri=4)
    {"days": range(0, 5), "start": ( 5, 30), "end": (19,  0)},
    # Saturday
    {"days": [5],         "start": ( 8,  0), "end": (16,  0)},
    # Sunday
    {"days": [6],         "start": ( 7,  0), "end": (12,  0)},
]


def _mins(h: int, m: int) -> int:
    return h * 60 + m


def in_recording_window(now: datetime) -> bool:
    """Return True if the recorder should currently be writing a file."""
    wd  = now.weekday()
    cur = _mins(now.hour, now.minute)
    for w in RECORDING_WINDOWS:
        if wd not in w["days"]:
            continue
        if _mins(*w["start"]) <= cur < _mins(*w["end"]):
            return True
    return False


# ── Notifications ─────────────────────────────────────────────────────────────

def notify(title: str, message: str, urgent: bool = False) -> None:
    """Send a macOS notification (no-op on Linux — log to stderr instead)."""
    if platform.system() == "Darwin":
        sound  = "Basso" if urgent else "Ping"
        script = (
            f'display notification "{message}" '
            f'with title "KTCK Recorder" '
            f'subtitle "{title}" '
            f'sound name "{sound}"'
        )
        subprocess.run(["osascript", "-e", script], capture_output=True)
    else:
        # Linux fallback — write to stderr so cron can email it
        print(f"[ALERT] {title}: {message}", file=sys.stderr)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts}  {msg}")


# ── Checks ────────────────────────────────────────────────────────────────────

def check_service() -> bool:
    """Return True if the launchd service is running (PID non-zero, last exit 0)."""
    try:
        # 'launchctl list' gives: PID  LastExitStatus  Label
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            if SERVICE_NAME not in line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            pid, exit_code = parts[0].strip(), parts[1].strip()
            # pid != "-" means process is running; exit_code == "0" means healthy
            return pid not in ("-", "0", "") and exit_code == "0"
    except FileNotFoundError:
        pass  # not macOS
    return True   # can't check on Linux; assume ok


def check_file_freshness(output_dir: Path, stale_mins: int) -> tuple[bool, str]:
    """
    Return (ok, message).
    Fails if the most recently modified .aac file hasn't changed in stale_mins
    AND we're currently in a recording window.
    """
    now = datetime.now(CT)
    if not in_recording_window(now):
        return True, "not in recording window — skipped"

    files = sorted(output_dir.glob("*.aac"), key=lambda f: f.stat().st_mtime)
    if not files:
        return False, f"no .aac files found in {output_dir}"

    newest   = files[-1]
    mtime    = datetime.fromtimestamp(newest.stat().st_mtime, tz=CT)
    age_mins = (now - mtime).total_seconds() / 60

    if age_mins > stale_mins:
        return False, (
            f"{newest.name} hasn't grown in {age_mins:.0f} min "
            f"(expected ≤{stale_mins} min)"
        )
    return True, f"{newest.name} updated {age_mins:.0f} min ago"


def check_disk_space(output_dir: Path, warn_gb: float) -> tuple[bool, str]:
    """Return (ok, message) based on free space on the output volume."""
    try:
        stat     = os.statvfs(output_dir)
        free_gb  = (stat.f_bavail * stat.f_frsize) / 1_073_741_824
        total_gb = (stat.f_blocks * stat.f_frsize) / 1_073_741_824
        pct_used = 100 * (1 - stat.f_bavail / stat.f_blocks)
        msg      = f"{free_gb:.1f} GB free of {total_gb:.0f} GB ({pct_used:.0f}% used)"
        if free_gb < warn_gb:
            return False, msg
        return True, msg
    except Exception as exc:
        return True, f"could not check disk: {exc}"   # non-fatal


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Health check for the KTCK recorder service.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output-dir",   type=Path,  default=DEFAULT_OUT_DIR)
    parser.add_argument("--disk-warn-gb", type=float, default=DISK_WARN_GB,
                        help="Alert when free space drops below N GB")
    parser.add_argument("--stale-mins",   type=int,   default=STALE_MINS,
                        help="Alert if newest file unchanged for N minutes during recording window")
    parser.add_argument("--test", action="store_true",
                        help="Force all three alerts (for notification testing)")
    args = parser.parse_args()

    log(f"Health check — {datetime.now(CT).strftime('%a %I:%M %p CDT')}")
    any_alert = False

    if args.test:
        notify("Service down", "recorder service not running", urgent=True)
        notify("Recording stalled", "no file growth in 20 min", urgent=True)
        notify("Low disk space", "30.0 GB free of 500 GB (94% used)", urgent=False)
        log("TEST: all three alerts fired")
        return

    # ── 1. Service ───────────────────────────────────────────────────────────
    svc_ok = check_service()
    if svc_ok:
        log("  ✔  Service running")
    else:
        log("  ✘  Service NOT running")
        notify("Service down",
               f"{SERVICE_NAME} is not running — check logs",
               urgent=True)
        any_alert = True

    # ── 2. File freshness ────────────────────────────────────────────────────
    fresh_ok, fresh_msg = check_file_freshness(args.output_dir, args.stale_mins)
    if fresh_ok:
        log(f"  ✔  File freshness: {fresh_msg}")
    else:
        log(f"  ✘  File freshness: {fresh_msg}")
        notify("Recording stalled", fresh_msg, urgent=True)
        any_alert = True

    # ── 3. Disk space ────────────────────────────────────────────────────────
    disk_ok, disk_msg = check_disk_space(args.output_dir, args.disk_warn_gb)
    if disk_ok:
        log(f"  ✔  Disk space: {disk_msg}")
    else:
        log(f"  ✘  Disk space: {disk_msg}")
        notify("Low disk space", disk_msg, urgent=False)
        any_alert = True

    if not any_alert:
        log("  All checks passed.")


if __name__ == "__main__":
    main()
