#!/usr/bin/env python3
"""
Verify the recorder pipeline end-to-end without relying on external process killing.

Tests:
  1. Stream connectivity  — ffmpeg can reach KTCK and write audio
  2. Full segment         — recorder produces a valid .aac file after a clean 15-second run
  3. SIGTERM shutdown     — recorder saves a partial file when interrupted mid-segment

Run:
    python3 verify_recording.py
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import recorder

OUTPUT_DIR  = Path("/tmp/ticket_verify")
OUTPUT_T1   = OUTPUT_DIR / "t1_stream"
OUTPUT_T2   = OUTPUT_DIR / "t2_full_segment"
OUTPUT_T3   = OUTPUT_DIR / "t3_sigterm"
for d in (OUTPUT_T1, OUTPUT_T2, OUTPUT_T3):
    d.mkdir(parents=True, exist_ok=True)

PASS = "\033[0;32m✔  PASS\033[0m"
FAIL = "\033[0;31m✘  FAIL\033[0m"


def probe(path: Path) -> dict:
    """Return duration (s) and size (bytes) for an audio file via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size",
            "-of", "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    info = {}
    for line in result.stdout.splitlines():
        k, _, v = line.partition("=")
        try:
            info[k.strip()] = float(v.strip())
        except ValueError:
            pass
    return info


def hr(label: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {label}")
    print('─' * 60)


# ── Test 1: Stream connectivity ───────────────────────────────────────────────

hr("Test 1 — Stream connectivity (10-second ffmpeg capture)")

conn_file = OUTPUT_T1 / "connectivity_check.aac"
cmd = recorder.build_cmd(conn_file, duration_secs=10)
print(f"  Command: {' '.join(cmd[:6])} ...")
rc = subprocess.run(cmd, capture_output=True).returncode
info = probe(conn_file) if conn_file.exists() else {}

if rc == 0 and info.get("duration", 0) >= 9 and info.get("size", 0) > 10_000:
    print(f"  {PASS}  {conn_file.name}  "
          f"{info['duration']:.1f}s  {info['size']/1024:.0f} KB")
else:
    print(f"  {FAIL}  exit={rc}  {info}")
    sys.exit(1)


# ── Test 2: Full segment via recorder (15 seconds, clean exit) ────────────────

hr("Test 2 — Full 15-second segment via recorder.record_loop")

# Patch: 15-second segments instead of 60 minutes
recorder.FALLBACK_SEGMENT_MINS = 15 / 60   # 0.25 → 15 seconds
recorder._shutdown = False

# Stop the loop after the first successful segment
_orig_run = recorder.run_ffmpeg
segments_done = [0]

def _counting_run(cmd):
    code = _orig_run(cmd)
    segments_done[0] += 1
    if segments_done[0] >= 1:
        recorder._shutdown = True   # stop after first segment
    return code

recorder.run_ffmpeg = _counting_run

print("  Recording 15-second test segment…")
recorder.record_loop(OUTPUT_T2, use_schedule=False)

# Restore
recorder.run_ffmpeg = _orig_run
recorder._shutdown  = False

files = sorted(OUTPUT_T2.glob("????-??-??_????_Broadcast.aac"))
if files:
    f    = files[-1]
    info = probe(f)
    if info.get("duration", 0) >= 12 and info.get("size", 0) > 10_000:
        print(f"  {PASS}  {f.name}  "
              f"{info['duration']:.1f}s  {info['size']/1024:.0f} KB")
    else:
        print(f"  {FAIL}  file exists but audio looks wrong: {info}")
        sys.exit(1)
else:
    print(f"  {FAIL}  no output file found in {OUTPUT_DIR}")
    sys.exit(1)


# ── Test 3: SIGTERM produces a partial file ───────────────────────────────────

hr("Test 3 — SIGTERM mid-segment saves partial file")

recorder.FALLBACK_SEGMENT_MINS = 60   # full 60-minute segment → we'll interrupt it
recorder._shutdown = False

print("  Starting recorder (60-min segment), sending SIGTERM after 12 s…")

def _send_sigterm():
    time.sleep(12)
    os.kill(os.getpid(), signal.SIGTERM)

t = threading.Thread(target=_send_sigterm, daemon=True)
t.start()
recorder.record_loop(OUTPUT_T3, use_schedule=False)

# Restore
recorder.FALLBACK_SEGMENT_MINS = 60
recorder._shutdown = False

partial_files = sorted(OUTPUT_T3.glob("????-??-??_????_Broadcast.aac"))
partial = partial_files[-1] if partial_files else None
if partial:
    info = probe(partial)
    if info.get("duration", 0) >= 8 and info.get("size", 0) > 5_000:
        print(f"  {PASS}  {partial.name}  "
              f"{info['duration']:.1f}s  {info['size']/1024:.0f} KB  (partial)")
    else:
        print(f"  {FAIL}  partial file looks empty: {info}")
        sys.exit(1)
else:
    print(f"  {FAIL}  no partial file found")
    sys.exit(1)


# ── Summary ───────────────────────────────────────────────────────────────────

hr("Results")
print(f"  Test 1  Stream connectivity   {PASS}")
print(f"  Test 2  Full segment          {PASS}")
print(f"  Test 3  SIGTERM partial save  {PASS}")
print(f"\n  Output files:")
for f in sorted(OUTPUT_DIR.rglob("*.aac")):
    info = probe(f)
    print(f"    {f.name:<50}  {info.get('duration', 0):.1f}s  "
          f"{info.get('size', 0)/1024:.0f} KB")
print()
