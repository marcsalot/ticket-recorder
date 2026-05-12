#!/usr/bin/env python3
"""
KTCK The Ticket – Live Stream Recorder
=======================================
Continuously records the KTCK live stream via ffmpeg, automatically
segmenting output files at show-schedule boundaries (Central Time).

Requirements:
    Python 3.9+
    ffmpeg on PATH  →  brew install ffmpeg

Usage:
    python recorder.py
    python recorder.py --output-dir ~/Dropbox/TheTicket
    python recorder.py --no-schedule        # fixed 1-hour segments
    python recorder.py --help
"""

import argparse
import json
import logging
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# ── Stream ────────────────────────────────────────────────────────────────────

STREAM_URL = (
    "https://playerservices.streamtheworld.com/api/livestream-redirect/KTCKAMAAC.aac"
)

# ── Defaults ──────────────────────────────────────────────────────────────────

TIMEZONE            = ZoneInfo("America/Chicago")   # Dallas / Central Time
DEFAULT_OUTPUT_DIR  = Path.home() / "ticket_recordings"
RECONNECT_DELAY       = 10    # seconds to wait between retries on stream failure
FALLBACK_SEGMENT_MINS = 60    # segment length (min) when no show matches schedule
OVERRIDES_FILE        = Path(__file__).parent / "overrides.json"  # custom event overrides
OVERRIDES_TIME_FMT    = "%Y-%m-%d %H:%M"   # format used in overrides.json

# ── Show Schedule (Central Time, 24-h clock) ──────────────────────────────────
# days  : list/range of 0-based weekday ints  (0=Mon … 6=Sun)
# start : (hour, minute) — start of show (inclusive)
# end   : (hour, minute) — end of show (exclusive); use (24, 0) for midnight
#
# Update this to match any schedule changes on theticket.com/schedule
# Source: https://www.theticket.com/full-programming-schedule
# "record": False  →  skip by default; still recordable via overrides.json
SHOW_SCHEDULE = [
    # ── Weekdays (Mon–Fri) ───────────────────────────────────────────────────
    {"days": range(0, 5), "start": ( 0,  0), "end": ( 3, 30), "name": "Fox Sports Radio",        "record": False},
    {"days": range(0, 5), "start": ( 3, 30), "end": ( 5, 30), "name": "The Ticket Top 10",        "record": False},
    {"days": range(0, 5), "start": ( 5, 30), "end": ( 6,  0), "name": "The Musers Pregame",       "record": True},
    {"days": range(0, 5), "start": ( 6,  0), "end": ( 9,  0), "name": "The Musers",               "record": True},
    {"days": range(0, 5), "start": ( 9,  0), "end": (13,  0), "name": "The Invasion",             "record": True},
    {"days": range(0, 5), "start": (13,  0), "end": (15,  0), "name": "The Sweet Spot",           "record": True},
    {"days": range(0, 5), "start": (15,  0), "end": (19,  0), "name": "The Hardline",             "record": True},
    {"days": range(0, 5), "start": (19,  0), "end": (21,  0), "name": "The Ticket Top 10",        "record": False},
    {"days": range(0, 5), "start": (21,  0), "end": (24,  0), "name": "Fox Sports Radio",         "record": False},
    # ── Saturday ─────────────────────────────────────────────────────────────
    {"days": [5], "start": ( 0,  0), "end": ( 8,  0), "name": "Fox Sports Radio",                 "record": False},
    {"days": [5], "start": ( 8,  0), "end": (10,  0), "name": "The Teebox",                       "record": True},
    {"days": [5], "start": (10,  0), "end": (12,  0), "name": "College Football Tailgate",        "record": True},
    {"days": [5], "start": (12,  0), "end": (14,  0), "name": "Afternoon Delight",               "record": True},
    {"days": [5], "start": (14,  0), "end": (16,  0), "name": "The Kick Around",                 "record": True},
    {"days": [5], "start": (16,  0), "end": (24,  0), "name": "Fox Sports Radio",                 "record": False},
    # ── Sunday ───────────────────────────────────────────────────────────────
    {"days": [6], "start": ( 0,  0), "end": ( 7,  0), "name": "Fox Sports Radio",                 "record": False},
    {"days": [6], "start": ( 7,  0), "end": ( 9,  0), "name": "Mark Elfenbein",                   "record": True},
    {"days": [6], "start": ( 9,  0), "end": (12,  0), "name": "Work in Progress",                "record": True},
    {"days": [6], "start": (12,  0), "end": (24,  0), "name": "Fox Sports Radio",                 "record": False},
]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ktck_recorder")

# ── Graceful Shutdown ─────────────────────────────────────────────────────────

_shutdown = False
_current_proc: Optional[subprocess.Popen] = None


def _handle_signal(sig, frame):
    global _shutdown
    log.info("Shutdown signal received — finishing current segment and stopping…")
    _shutdown = True
    if _current_proc and _current_proc.poll() is None:
        _current_proc.terminate()


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ── Schedule Helpers ──────────────────────────────────────────────────────────


def _mins(hour: int, minute: int) -> int:
    """Convert (hour, minute) to total minutes since midnight."""
    return hour * 60 + minute


def get_show_info(now: datetime) -> tuple[str, datetime, bool]:
    """
    Return ``(show_name, segment_end_datetime, should_record)`` for the current moment.
    Falls back to a FALLBACK_SEGMENT_MINS-length unrecorded segment if nothing matches.
    """
    weekday  = now.weekday()
    cur_mins = now.hour * 60 + now.minute

    for show in SHOW_SCHEDULE:
        if weekday not in show["days"]:
            continue

        start_m       = _mins(*show["start"])
        end_h, end_m  = show["end"]
        end_total     = _mins(end_h, end_m)  # 1440 when end=(24,0)

        if start_m <= cur_mins < min(end_total, 1440):
            if end_h == 24:
                end_dt = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            else:
                end_dt = now.replace(
                    hour=end_h, minute=end_m, second=0, microsecond=0
                )
            return show["name"], end_dt, show.get("record", True)

    # Fallback: fixed-length unrecorded segment
    end_dt = (now + timedelta(minutes=FALLBACK_SEGMENT_MINS)).replace(
        second=0, microsecond=0
    )
    return "Unscheduled", end_dt, False


# ── Overrides ─────────────────────────────────────────────────────────────────


def load_overrides() -> list[dict]:
    """
    Load custom recording events from overrides.json (if it exists).
    Each entry must have:
        name  : display name for the recording
        start : "YYYY-MM-DD HH:MM"  (Central Time)
        end   : "YYYY-MM-DD HH:MM"  (Central Time) — can be the next day for overnights

    Example overrides.json:
    [
      {"name": "Cowboys Preseason",    "start": "2026-08-10 18:30", "end": "2026-08-10 23:00"},
      {"name": "All Day Saturday",     "start": "2026-07-04 00:00", "end": "2026-07-05 00:00"},
      {"name": "New Year Eve Special", "start": "2026-12-31 21:00", "end": "2027-01-01 03:00"}
    ]
    """
    if not OVERRIDES_FILE.exists():
        return []
    try:
        with OVERRIDES_FILE.open() as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Could not load %s: %s", OVERRIDES_FILE.name, exc)
        return []


def get_active_override(now: datetime) -> Optional[tuple[str, datetime]]:
    """Return ``(name, end_datetime)`` if an override is active right now, else None."""
    for entry in load_overrides():
        try:
            start_dt = datetime.strptime(entry["start"], OVERRIDES_TIME_FMT).replace(tzinfo=TIMEZONE)
            end_dt   = datetime.strptime(entry["end"],   OVERRIDES_TIME_FMT).replace(tzinfo=TIMEZONE)
        except (KeyError, ValueError) as exc:
            log.warning("Skipping malformed override entry %r: %s", entry, exc)
            continue
        if start_dt <= now < end_dt:
            return entry["name"], end_dt
    return None


def safe_name(text: str) -> str:
    """Strip special characters and replace spaces for use in a filename."""
    return re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "_")


def make_output_path(output_dir: Path, show: str, start: datetime) -> Path:
    fname = f"{start.strftime('%Y-%m-%d_%H%M')}_{safe_name(show)}.aac"
    return output_dir / fname

# ── ffmpeg ────────────────────────────────────────────────────────────────────


def build_cmd(output_path: Path, duration_secs: int) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        # Reconnect automatically if the stream drops
        "-reconnect",         "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "30",
        # Input stream
        "-i", STREAM_URL,
        # Record for this many seconds then stop
        "-t", str(max(duration_secs, 1)),
        # Copy audio without re-encoding (preserves original quality)
        "-c", "copy",
        # Overwrite without prompting
        "-y",
        str(output_path),
    ]


def run_ffmpeg(cmd: list[str]) -> int:
    """Spawn ffmpeg and block until it exits. Returns the exit code.

    ``start_new_session=True`` puts ffmpeg in its own process group so that
    terminal signals (SIGINT/SIGTERM) sent to the Python process do NOT
    propagate directly to ffmpeg.  Python's signal handler then controls the
    clean shutdown by calling ``_current_proc.terminate()`` explicitly.
    """
    global _current_proc
    _current_proc = subprocess.Popen(cmd, start_new_session=True)
    code = _current_proc.wait()
    _current_proc = None
    return code


def sleep_until(end_dt: datetime) -> None:
    """
    Sleep in 60-second increments until end_dt (or shutdown).
    Re-reads overrides.json on each tick so a new override activates within ~60 seconds
    even while sleeping through a skipped slot (e.g. a 2-hour Fox Sports Radio block).
    """
    while not _shutdown:
        remaining = (end_dt - datetime.now(TIMEZONE)).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(60, remaining))
        # Re-check overrides after each sleep tick; break early if one is now active
        if get_active_override(datetime.now(TIMEZONE)):
            log.info("Override detected — waking from skipped slot early")
            break


# ── Main Loop ─────────────────────────────────────────────────────────────────


def record_loop(output_dir: Path, use_schedule: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    log.info("Output directory : %s", output_dir)
    log.info("Stream URL       : %s", STREAM_URL)
    log.info("Schedule mode    : %s", "on" if use_schedule else "off (fixed segments)")

    while not _shutdown:
        now = datetime.now(TIMEZONE)

        # ── 1. Check for active override (always recorded) ───────────────────
        override = get_active_override(now) if use_schedule else None
        if override:
            show_name, end_dt = override
            duration_secs     = max(int((end_dt - now).total_seconds()), 1)
            log.info(
                "★  Override '%s'  →  %s  (~%d min, ends ~%s)",
                show_name,
                make_output_path(output_dir, show_name, now).name,
                duration_secs // 60,
                end_dt.strftime("%H:%M"),
            )
        # ── 2. Regular schedule slot ─────────────────────────────────────────
        elif use_schedule:
            show_name, end_dt, should_record = get_show_info(now)
            duration_secs = max(int((end_dt - now).total_seconds()), 1)
            if not should_record:
                log.debug(
                    "–  Skipping '%s' (ends ~%s) — sleeping",
                    show_name, end_dt.strftime("%H:%M"),
                )
                sleep_until(end_dt)
                continue
        # ── 3. No-schedule mode: fixed-length segments ───────────────────────
        else:
            show_name     = "Broadcast"
            duration_secs = FALLBACK_SEGMENT_MINS * 60
            end_dt        = now + timedelta(seconds=duration_secs)

        output_path = make_output_path(output_dir, show_name, now)
        cmd         = build_cmd(output_path, duration_secs)

        log.info(
            "▶  '%s'  →  %s  (~%d min, ends ~%s)",
            show_name,
            output_path.name,
            duration_secs // 60,
            end_dt.strftime("%H:%M"),
        )

        returncode = run_ffmpeg(cmd)

        if _shutdown:
            log.info("Partial segment saved: %s", output_path.name)
            break

        if returncode == 0:
            log.info("✔  Segment complete: %s", output_path.name)
            time.sleep(1)   # brief pause before starting the next segment
        else:
            log.warning(
                "ffmpeg exited with code %d. Retrying in %ds…",
                returncode, RECONNECT_DELAY,
            )
            time.sleep(RECONNECT_DELAY)

    log.info("Recorder stopped.")

# ── Entry Point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record the KTCK live stream, segmented by show schedule.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        metavar="DIR",
        help="Directory where .aac files are saved",
    )
    parser.add_argument(
        "--no-schedule",
        action="store_true",
        help=f"Ignore show schedule; use fixed {FALLBACK_SEGMENT_MINS}-min segments",
    )
    args = parser.parse_args()

    record_loop(args.output_dir, use_schedule=not args.no_schedule)


if __name__ == "__main__":
    main()
