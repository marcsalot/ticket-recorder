#!/usr/bin/env python3
"""
KTCK Recording Retention Policy
================================
Deletes .aac and .mp3 recordings older than RETENTION_DAYS from the output directory.
Safe to run as a scheduled daily job (cron / launchd).

Usage:
    python3 cleanup.py                                   # delete > 30 days
    python3 cleanup.py --days 60                         # keep 60 days
    python3 cleanup.py --dry-run                         # preview without deleting
    python3 cleanup.py --output-dir ~/Dropbox/TheTicket  # custom directory
"""

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT_DIR   = Path.home() / "ticket_recordings"
DEFAULT_RETENTION    = 30   # days

# ── Special-event detection ───────────────────────────────────────────────────
# Show names as produced by recorder.py's safe_name() for every entry in
# SHOW_SCHEDULE, plus internal names used by the recorder itself.
# Any .aac file whose embedded show name is NOT in this set was recorded via
# overrides.json and is treated as a "special event".
#
# Keep this in sync with SHOW_SCHEDULE in recorder.py if you add new shows.
REGULAR_SHOW_NAMES = frozenset({
    # Weekdays
    "Fox_Sports_Radio", "The_Ticket_Top_10", "The_Musers_Pregame",
    "The_Musers", "The_Invasion", "The_Sweet_Spot", "The_Hardline",
    # Saturday
    "The_Teebox", "College_Football_Tailgate",
    "Afternoon_Delight", "The_Kick_Around",
    # Sunday
    "Mark_Elfenbein", "Work_in_Progress",
    # Internal recorder names
    "Broadcast",    # --no-schedule mode
    "Unscheduled",  # fallback slot
})

# Filename format written by recorder.py:
#   Normal:    YYYY-MM-DD_HHMM_ShowName.aac
#   Collision: YYYY-MM-DD_HHMM_ShowName_2.aac  (same-minute duplicate counter)
_FNAME_RE    = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_(.+)$")
_COUNTER_RE  = re.compile(r"_\d+$")   # strip trailing _N counter if present


def _show_name(f: Path) -> Optional[str]:
    """Extract the show name slug, stripping any same-minute collision counter."""
    m = _FNAME_RE.match(f.stem)
    if not m:
        return None
    return _COUNTER_RE.sub("", m.group(1))


def _is_special(f: Path) -> bool:
    """Return True if the file looks like an override/special-event recording."""
    name = _show_name(f)
    return name is not None and name not in REGULAR_SHOW_NAMES

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ktck_cleanup")


def _fmt(n_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


# ── Core logic ────────────────────────────────────────────────────────────────

def cleanup(output_dir: Path, retention_days: int, dry_run: bool,
            keep_special: bool = False) -> int:
    """
    Delete .aac and .mp3 files older than ``retention_days`` in ``output_dir``.

    When ``keep_special=True``, any file whose show name is not in
    REGULAR_SHOW_NAMES (i.e. was recorded via an override) is never deleted.

    Returns the number of files deleted (or that would be deleted in dry-run).
    """
    if not output_dir.exists():
        log.error("Directory not found: %s", output_dir)
        return 0

    cutoff  = datetime.now(timezone.utc) - timedelta(days=retention_days)
    verb    = "WOULD DELETE" if dry_run else "Deleted"

    log.info("Directory  : %s", output_dir)
    log.info("Retain     : %d days  (cutoff: %s UTC)",
             retention_days, cutoff.strftime("%Y-%m-%d %H:%M"))
    if dry_run:
        log.info("Mode       : DRY RUN — nothing will be deleted")
    if keep_special:
        log.info("Keep special: yes — override recordings will never be deleted")

    # rglob handles any future subfolder structure transparently
    all_files = sorted(
        list(output_dir.rglob("*.aac")) + list(output_dir.rglob("*.mp3"))
    )
    if not all_files:
        log.info("No .aac or .mp3 files found.")
        return 0

    deleted_count = deleted_bytes = 0
    kept_count    = kept_bytes    = 0
    special_count = special_bytes = 0

    for f in all_files:
        stat   = f.stat()
        mtime  = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        size   = stat.st_size
        age    = (datetime.now(timezone.utc) - mtime).days

        # Always keep special-event recordings when flag is set
        if keep_special and _is_special(f):
            log.info("  %-12s  %-55s  %3d days  %s",
                     "KEPT(special)", f.name, age, _fmt(size))
            special_count += 1
            special_bytes += size
            kept_count    += 1
            kept_bytes    += size
            continue

        if mtime < cutoff:
            log.info("  %-12s  %-55s  %3d days  %s",
                     verb, f.name, age, _fmt(size))
            if not dry_run:
                f.unlink()
            deleted_count += 1
            deleted_bytes += size
        else:
            kept_count += 1
            kept_bytes += size

    log.info("")
    if dry_run:
        log.info("Would delete : %3d file(s)   %s", deleted_count, _fmt(deleted_bytes))
    else:
        log.info("Deleted      : %3d file(s)   %s freed", deleted_count, _fmt(deleted_bytes))
    if keep_special and special_count:
        log.info("Special kept : %3d file(s)   %s  (override recordings)",
                 special_count, _fmt(special_bytes))
    log.info("Retained     : %3d file(s)   %s", kept_count, _fmt(kept_bytes))

    return deleted_count


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete KTCK .aac recordings older than RETENTION days.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        metavar="DIR",
        help="Directory to scan (searched recursively)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_RETENTION,
        metavar="N",
        help="Retain files newer than N days; delete the rest",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted without touching any files",
    )
    parser.add_argument(
        "--keep-special",
        action="store_true",
        help="Never delete override/special-event recordings regardless of age",
    )
    args = parser.parse_args()
    cleanup(args.output_dir, args.days, args.dry_run, args.keep_special)


if __name__ == "__main__":
    main()
