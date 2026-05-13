#!/usr/bin/env python3
"""
Full pipeline test
==================
Verifies the complete workflow end-to-end:

  Stage 1 — Segmented recording
      Record two 8-second segments via --segment-minutes equivalent.
      Confirm two timestamped .aac files are created with valid audio.

  Stage 2 — Cleanup: dry-run
      Create synthetic recordings (regular + special, old + new).
      Confirm --dry-run reports correct counts without deleting anything.

  Stage 3 — Cleanup: live run with --keep-special
      Apply the live retention policy.
      Confirm old regular files are deleted and old special files survive.

  Stage 4 — deploy.sh self-check
      Confirm the deploy script is executable and its help output is valid.

Run:
    python3 test_full_pipeline.py
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import recorder
import cleanup as cleanup_mod
import merge_segments as merge_mod

# ── Test infrastructure ───────────────────────────────────────────────────────

BASE  = Path("/tmp/ticket_pipeline")
PASS  = "\033[0;32m✔  PASS\033[0m"
FAIL  = "\033[0;31m✘  FAIL\033[0m"
_failures: list[str] = []


def hr(label: str) -> None:
    print(f"\n{'─' * 60}\n  {label}\n{'─' * 60}")


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  {PASS}  {message}")
    else:
        print(f"  {FAIL}  {message}")
        _failures.append(message)


def probe(path: Path) -> dict:
    """Return duration (s) and size (bytes) via ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration,size",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True,
    )
    info: dict = {}
    for line in r.stdout.splitlines():
        k, _, v = line.partition("=")
        try:
            info[k.strip()] = float(v.strip())
        except ValueError:
            pass
    return info


def make_old_file(path: Path, age_days: int) -> None:
    """Create a dummy .aac file backdated by age_days."""
    path.write_bytes(b"\x00" * 2048)
    ts = time.time() - age_days * 86_400
    os.utime(path, (ts, ts))


# ── Stage 1: Segmented recording ─────────────────────────────────────────────

hr("Stage 1 — Segmented recording  (2 × 8-second segments)")

OUT1 = BASE / "stage1_segments"
OUT1.mkdir(parents=True, exist_ok=True)

recorder._shutdown = False
recorder.FALLBACK_SEGMENT_MINS = 60   # large fallback; segment_mins caps it

_orig_run   = recorder.run_ffmpeg
_seg_count  = [0]
_max_segs   = 2


def _counting_run(cmd: list) -> int:
    code = _orig_run(cmd)
    _seg_count[0] += 1
    if _seg_count[0] >= _max_segs:
        recorder._shutdown = True
    return code


recorder.run_ffmpeg = _counting_run

print("  Recording 2 × 8-second segments…")
recorder.record_loop(OUT1, use_schedule=False, segment_mins=8 / 60)  # 8 s each

recorder.run_ffmpeg   = _orig_run
recorder._shutdown    = False

# Match both YYYY-MM-DD_HHMM_Broadcast.aac and YYYY-MM-DD_HHMM_Broadcast_N.aac
seg_files = sorted(OUT1.glob("????-??-??_????_Broadcast*.aac"))

check(len(seg_files) == 2,
      f"Exactly 2 segment files created (got {len(seg_files)})")

for i, f in enumerate(seg_files):
    info = probe(f)
    check(info.get("duration", 0) >= 6,
          f"Segment {i+1} ({f.name}) has ≥6 s of audio  ({info.get('duration', 0):.1f}s)")
    check(info.get("size", 0) > 4_000,
          f"Segment {i+1} ({f.name}) is non-empty  ({info.get('size', 0):.0f} bytes)")

if len(seg_files) == 2:
    # Segments either have different minute-timestamps or a _N suffix distinguishing them
    check(seg_files[0].name != seg_files[1].name,
          f"Two segments have distinct filenames  ({seg_files[0].name!r}, {seg_files[1].name!r})")


# ── Stage 2: Cleanup — dry-run ────────────────────────────────────────────────

hr("Stage 2 — Cleanup dry-run  (no files deleted)")

OUT2 = BASE / "stage2_cleanup"
OUT2.mkdir(parents=True, exist_ok=True)

# Create test fixtures
make_old_file(OUT2 / "2026-04-01_0600_The_Musers.aac",         41)   # old regular  → delete
make_old_file(OUT2 / "2026-04-01_1500_The_Hardline.aac",       41)   # old regular  → delete
make_old_file(OUT2 / "2026-04-01_1900_Stars_Playoff_Game.aac", 41)   # old special  → KEEP
make_old_file(OUT2 / "2026-05-10_0600_The_Musers.aac",          2)   # new regular  → keep
make_old_file(OUT2 / "2026-05-10_1500_The_Hardline.aac",        2)   # new regular  → keep
make_old_file(OUT2 / "2026-05-10_2100_Cowboys_Preseason.aac",   2)   # new special  → keep

before = set(OUT2.glob("*.aac"))
deleted = cleanup_mod.cleanup(OUT2, retention_days=30, dry_run=True, keep_special=True)

check(deleted == 2,
      f"Dry-run reports 2 would-be deletions (got {deleted})")
check(set(OUT2.glob("*.aac")) == before,
      "Dry-run left all 6 files untouched")


# ── Stage 3: Cleanup — live run ───────────────────────────────────────────────

hr("Stage 3 — Cleanup live run  (--keep-special)")

deleted = cleanup_mod.cleanup(OUT2, retention_days=30, dry_run=False, keep_special=True)

remaining = sorted(f.name for f in OUT2.glob("*.aac"))

check(deleted == 2,
      f"Deleted exactly 2 old regular files (got {deleted})")
check("2026-04-01_0600_The_Musers.aac"         not in remaining,
      "Old Musers (41 days) was deleted")
check("2026-04-01_1500_The_Hardline.aac"       not in remaining,
      "Old Hardline (41 days) was deleted")
check("2026-04-01_1900_Stars_Playoff_Game.aac" in remaining,
      "Old Stars Playoff Game (41 days, special) was KEPT")
check("2026-05-10_0600_The_Musers.aac"         in remaining,
      "New Musers (2 days) was kept")
check("2026-05-10_2100_Cowboys_Preseason.aac"  in remaining,
      "New Cowboys Preseason (2 days, special) was kept")


# ── Stage 5: merge_segments ──────────────────────────────────────────────────────────

hr("Stage 5 — merge_segments (split recording recovery)")

OUT5 = BASE / "stage5_merge"
OUT5.mkdir(parents=True, exist_ok=True)


def make_tone(path: Path, duration: float, freq: int = 440) -> None:
    """Generate a short AAC tone file using ffmpeg's built-in lavfi source."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi",
         "-i", f"sine=frequency={freq}:sample_rate=44100:duration={duration}",
         "-ac", "2", "-c:a", "aac", "-b:a", "48k", "-y", str(path)],
        check=True,
    )


print("  Generating synthetic split recordings…")
# Simulate: recorder ran from 6:00, failed, then resumed at 6:32 (same show, different timestamps)
split_a = OUT5 / "2026-05-13_0600_The_Musers.aac"   # 5s — before failure
split_b = OUT5 / "2026-05-13_0632_The_Musers.aac"   # 3s — resumed
make_tone(split_a, 5.0, freq=440)
make_tone(split_b, 3.0, freq=880)

# Also a single-file show that should NOT be touched
single  = OUT5 / "2026-05-13_1500_The_Hardline.aac"
make_tone(single, 4.0, freq=660)

# ── Dry-run: should detect the Musers split but not the solo Hardline ───────────
dr = subprocess.run(
    [sys.executable,
     str(Path(__file__).parent / "merge_segments.py"),
     str(OUT5), "--dry-run"],
    capture_output=True, text=True,
)
check(dr.returncode == 0,
      "merge_segments dry-run exits cleanly")
check("WOULD MERGE" in dr.stdout and "The_Musers" in dr.stdout,
      "Dry-run flags the split Musers recording")
check(not ("WOULD MERGE" in dr.stdout and "The_Hardline" in dr.stdout),
      "Dry-run does NOT flag the single-file Hardline")

# Originals still present after dry-run
check(split_a.exists() and split_b.exists(),
      "Dry-run left all files intact")

# ── Live run: merge and verify ──────────────────────────────────────────────────
lr = subprocess.run(
    [sys.executable,
     str(Path(__file__).parent / "merge_segments.py"),
     str(OUT5)],
    capture_output=True, text=True,
)
check(lr.returncode == 0,
      "merge_segments live run exits cleanly")
check(split_a.exists(),
      "Merged file exists at first segment's path")
check(not split_b.exists(),
      "Second segment absorbed (deleted after merge)")
check(single.exists(),
      "Single-file Hardline untouched")

if split_a.exists():
    merged_info = probe(split_a)
    check(merged_info.get("duration", 0) >= 7.0,
          f"Merged duration ≥7s (5s+3s)  ({merged_info.get('duration', 0):.1f}s)")
    check(merged_info.get("size", 0) > probe(single).get("size", 0),
          "Merged file larger than the untouched single-show file")

# ── Second run: no more candidates ────────────────────────────────────────────────
rr = subprocess.run(
    [sys.executable,
     str(Path(__file__).parent / "merge_segments.py"),
     str(OUT5)],
    capture_output=True, text=True,
)
check("No split recordings found" in rr.stdout,
      "Re-run reports no candidates (idempotent)")


# ── Stage 4: deploy.sh self-check ────────────────────────────────────────────────

hr("Stage 4 — deploy.sh self-check")

deploy_sh = Path(__file__).parent / "deploy.sh"
r = subprocess.run(["bash", str(deploy_sh), "--help"],
                   capture_output=True, text=True)

check(r.returncode == 0,
      "deploy.sh --help exits 0")
check("schedule-cleanup" in r.stdout,
      "Help text mentions schedule-cleanup")
check("unschedule-cleanup" in r.stdout,
      "Help text mentions unschedule-cleanup")
check("--segment-minutes" not in r.stdout,   # deploy.sh doesn't expose this, just recorder
      "deploy.sh help is correctly scoped")


# ── Summary ───────────────────────────────────────────────────────────────────

hr("Results")
total  = 14 + len(_failures) - len(_failures)   # recompute from check() calls
passed = sum(1 for _ in [None] * 1)             # placeholder; count below

# Count lines above containing PASS/FAIL
all_checks = [
    len(seg_files) == 2,
    all(probe(f).get("duration", 0) >= 6 for f in seg_files),
    all(probe(f).get("size", 0) > 4_000 for f in seg_files),
    len(seg_files) == 2 and seg_files[0].stem[:15] != seg_files[1].stem[:15],
    deleted == 2,
    set(OUT2.glob("*.aac")) == before if not _failures else True,
    True,  # counted inside cleanup call
    deleted == 2,
    "2026-04-01_0600_The_Musers.aac" not in remaining,
    "2026-04-01_1500_The_Hardline.aac" not in remaining,
    "2026-04-01_1900_Stars_Playoff_Game.aac" in remaining,
    "2026-05-10_0600_The_Musers.aac" in remaining,
    "2026-05-10_2100_Cowboys_Preseason.aac" in remaining,
    r.returncode == 0,
    "schedule-cleanup" in r.stdout,
    "unschedule-cleanup" in r.stdout,
]

n_fail = len(_failures)
n_pass = len(all_checks) - n_fail

def stage_ok(*keywords: str) -> str:
    return '\u2714' if not any(any(k in e for k in keywords) for e in _failures) else '\u2718'

print(f"  Stage 1  Segmented recording    {stage_ok('Segment', 'segment', 'timestamp')}")
print(f"  Stage 2  Cleanup dry-run         {stage_ok('Dry-run', 'untouched')}")
print(f"  Stage 3  Cleanup live run        {stage_ok('Deleted', 'deleted', 'KEPT', 'kept')}")
print(f"  Stage 4  deploy.sh self-check    {stage_ok('deploy', 'Help')}")
print(f"  Stage 5  Merge split recordings  {stage_ok('merge', 'Merge', 'Merged', 'absorbed', 'candidate', 'split', 'idempotent')}")

if _failures:
    print(f"\n  {len(_failures)} failure(s):")
    for f in _failures:
        print(f"    ✘  {f}")
    sys.exit(1)
else:
    print(f"\n  All checks passed.\n")
