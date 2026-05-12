#!/usr/bin/env python3
"""
monitor_transition.py — Watch for a show rollover and report when it happens.

Polls every 30 seconds, prints file sizes at 5-minute intervals, and
prints a clear banner the moment a new .aac file appears in the output dir.

Usage:
    python3 monitor_transition.py                         # watches ~/Dropbox/TheTicket
    python3 monitor_transition.py ~/Documents/TheTicket   # custom dir
    python3 monitor_transition.py --stop-after 15:10      # stop at HH:MM CDT
"""

import glob
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

CT       = ZoneInfo("America/Chicago")
OUT_DIR  = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
           else os.path.expanduser("~/Dropbox/TheTicket")

# Optional --stop-after HH:MM
STOP_AT  = None
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == "--stop-after" and i < len(sys.argv):
        h, m = map(int, sys.argv[i + 1].split(":"))
        STOP_AT = (h, m)


def fmt_size(b: int) -> str:
    return f"{b/1024/1024:.2f} MB" if b >= 1_048_576 else f"{b/1024:.0f} KB"


def get_files() -> list[str]:
    return sorted(glob.glob(os.path.join(OUT_DIR, "*.aac")))


def last_log_lines(n: int = 2) -> list[str]:
    log = os.path.expanduser("~/ticket-recorder/logs/recorder.log")
    if not os.path.exists(log):
        return []
    with open(log) as f:
        lines = f.readlines()
    return [l.strip() for l in lines if any(k in l for k in ("▶", "✔", "★", "Segment", "stopped"))][-n:]


def print_status(label: str = "") -> None:
    now   = datetime.now(CT)
    files = get_files()
    header = f"[{now.strftime('%I:%M:%S %p CDT')}]"
    if label:
        header += f"  {label}"
    print(f"\n{header}")
    if files:
        for f in files:
            size = os.path.getsize(f)
            mtime = datetime.fromtimestamp(os.path.getmtime(f), tz=CT)
            print(f"  {os.path.basename(f):<52}  {fmt_size(size):>8}  modified {mtime.strftime('%H:%M:%S')}")
    else:
        print("  (no .aac files yet)")
    for line in last_log_lines():
        print(f"  → {line}")


# ── Main loop ─────────────────────────────────────────────────────────────────

print(f"Monitoring: {OUT_DIR}")
print(f"Polling every 30 s — watching for show rollover")
if STOP_AT:
    print(f"Will stop at {STOP_AT[0]:02d}:{STOP_AT[1]:02d} CDT")
print()

prev_files = set(get_files())
print_status("initial state")

last_5min_report = datetime.now(CT).minute

while True:
    time.sleep(30)
    now          = datetime.now(CT)
    current_files = set(get_files())
    new_files    = current_files - prev_files

    if new_files:
        print(f"\n{'=' * 62}")
        print(f"  ✔  ROLLOVER at {now.strftime('%I:%M:%S %p CDT')}")
        print(f"  New file(s): {', '.join(os.path.basename(f) for f in sorted(new_files))}")
        print(f"{'=' * 62}")
        print_status()
        prev_files = current_files

    elif now.minute % 5 == 0 and now.minute != last_5min_report:
        print_status()
        last_5min_report = now.minute

    # Auto-stop
    if STOP_AT and (now.hour, now.minute) >= STOP_AT:
        print(f"\nReached stop time {STOP_AT[0]:02d}:{STOP_AT[1]:02d} CDT — monitor complete.")
        break
