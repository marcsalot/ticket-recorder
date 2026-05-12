#!/usr/bin/env python3
"""
strip_commercials.py — Remove commercial breaks from KTCK recordings.

Uses two independent signals:
  1. Silence detection   — finds the transition points between content and ads
  2. RMS loudness        — on KTCK, show content is louder than commercials;
                           any segment that is quieter than the show baseline
                           AND fits a commercial-length window is classified as
                           a break and excised.

A 75th-percentile RMS baseline (not a mean) is used so that a recording with
many breaks doesn't drag the reference level down and miss detections.

Usage:
    python3 strip_commercials.py 2026-05-12_1500_The_Hardline.aac
    python3 strip_commercials.py recording.aac --output clean.aac
    python3 strip_commercials.py recording.aac --dry-run        # show cuts only
    python3 strip_commercials.py recording.aac --threshold 4    # stricter
    python3 strip_commercials.py recording.aac --silence-db -35 # noisier signal

Requirements:
    ffmpeg + ffprobe on PATH
"""

import argparse
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path


# ── Defaults ──────────────────────────────────────────────────────────────────

SILENCE_DB        = -40     # dBFS — level below which audio is "silence"
SILENCE_DURATION  = 0.5     # seconds — minimum silence to be a boundary
MIN_COMMERCIAL    = 60      # seconds — shortest plausible commercial break
MAX_COMMERCIAL    = 420     # seconds — longest plausible commercial break (7 min)
LUFS_THRESHOLD    = 3.0     # dB — optional RMS gate (set 0 to disable)
MERGE_GAP         = 5.0     # seconds — merge adjacent commercial segments

# Cluster detection parameters
MAX_SPOT_DUR      = 60      # seconds — individual spot (or sub-spot silence-chunk)
                             # longer than this = definitely show content
MAX_INTRA_GAP     = 12      # seconds — max silence gap *inside* a commercial break


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

def get_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def detect_silences(path: Path, noise_db: float,
                    min_dur: float) -> list[tuple[float, float]]:
    """Return list of (silence_start, silence_end) tuples."""
    r = subprocess.run(
        ["ffmpeg", "-i", str(path),
         "-af", f"silencedetect=noise={noise_db}dB:duration={min_dur}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    output = r.stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", output)]
    ends   = [float(m) for m in re.findall(r"silence_end: ([\d.]+)",   output)]
    return list(zip(starts, ends))


def measure_rms(path: Path, start: float, end: float) -> float:
    """
    Return the mean RMS level (dBFS) of the segment [start, end].
    Uses ffmpeg astats — runs much faster than realtime for AAC.
    Returns -99.0 if the segment is silent or measurement fails.
    """
    duration = max(end - start, 0.1)
    r = subprocess.run(
        ["ffmpeg",
         "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
         "-i", str(path),
         "-af", ("astats=metadata=1:reset=1,"
                 "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"),
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    hits = re.findall(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+)", r.stdout)
    vals = [float(v) for v in hits]
    return statistics.mean(vals) if vals else -99.0


# ── Segment logic ─────────────────────────────────────────────────────────────

def silences_to_segments(silences: list[tuple[float, float]],
                          total: float) -> list[tuple[float, float]]:
    """Convert silence intervals to content segments (the audio between them)."""
    segs, prev = [], 0.0
    for ss, se in sorted(silences):
        if ss > prev + 0.05:
            segs.append((prev, ss))
        prev = se
    if prev < total - 0.05:
        segs.append((prev, total))
    return segs


def cluster_commercials(
    segments: list[tuple[float, float]],
    rms_vals: list[float],
    max_spot: float,
    max_gap: float,
    min_comm: float,
    max_comm: float,
    lufs_threshold: float,
) -> list[tuple[float, float, bool]]:
    """
    Two-signal commercial detection:

    Signal 1 — FRAGMENTATION CLUSTERING
      AAC radio streams rarely have true silence; instead a commercial break
      manifests as a dense cluster of short silence-delimited sub-segments
      (individual spots + inter-spot jingles).  We walk the segment list and
      greedily merge adjacent short segments (dur ≤ max_spot, gap ≤ max_gap)
      into candidate "commercial zones".  A zone whose merged duration falls
      in [min_comm, max_comm] is a break candidate.

    Signal 2 — RMS GATE (optional, set --threshold 0 to disable)
      On KTCK the show hosts are louder than most commercials.  The zone's
      average RMS is compared against a 75th-percentile baseline derived from
      the long (clearly-content) segments.  If lufs_threshold > 0, a break
      candidate is accepted only if its RMS is below baseline − threshold.
      Setting threshold=0 disables this gate and accepts all clustered zones.
    """
    if not segments:
        return []

    # ── Build content RMS baseline from long segments ─────────────────────────
    long_rms = [rms for (s, e), rms in zip(segments, rms_vals)
                if (e - s) > max_comm]
    if long_rms:
        long_rms.sort()
        baseline = long_rms[int(len(long_rms) * 0.75)]
    else:
        # Fallback: 75th percentile weighted by duration
        weighted = []
        for (s, e), rms in zip(segments, rms_vals):
            weighted.extend([rms] * max(1, int(e - s)))
        weighted.sort()
        baseline = weighted[int(len(weighted) * 0.75)] if weighted else -20.0

    # ── Cluster walk ──────────────────────────────────────────────────────────
    # We'll produce a flat list of (start, end, is_commercial)
    result: list[tuple[float, float, bool]] = []
    i = 0
    while i < len(segments):
        seg_start, seg_end = segments[i]
        dur = seg_end - seg_start

        if dur > max_spot:
            # Long segment — definitely content, emit as-is
            result.append((seg_start, seg_end, False))
            i += 1
            continue

        # Short segment — try to build a cluster
        zone_start = seg_start
        zone_end   = seg_end
        zone_rms   = [rms_vals[i]]
        j = i + 1

        while j < len(segments):
            nxt_start, nxt_end = segments[j]
            nxt_dur = nxt_end - nxt_start
            gap     = nxt_start - zone_end

            if nxt_dur > max_spot:
                # Next segment is long — it's content, stop the cluster here
                break
            if gap > max_gap:
                # Gap too wide — different cluster or content pause
                break

            zone_end = nxt_end
            zone_rms.append(rms_vals[j])
            j += 1

        zone_dur     = zone_end - zone_start
        zone_avg_rms = statistics.mean(zone_rms) if zone_rms else -99.0

        in_duration_window = min_comm <= zone_dur <= max_comm
        rms_ok = (
            lufs_threshold <= 0                      # gate disabled
            or zone_avg_rms < baseline - lufs_threshold
        )

        if in_duration_window and rms_ok:
            result.append((zone_start, zone_end, True))
            i = j
        else:
            # Not a commercial zone — emit the first segment as content
            # and re-try from i+1 so short content pauses don't get swallowed
            result.append((seg_start, seg_end, False))
            i += 1

    return result


# ── Output ────────────────────────────────────────────────────────────────────

def fmt_time(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def print_report(classified: list[tuple[float, float, bool]],
                 rms_vals: list[float]) -> None:
    total_content = total_commercial = 0.0
    print(f"\n{'Start':>8}  {'End':>8}  {'Dur':>6}  {'RMS':>6}  Type")
    print("─" * 46)
    for (start, end, is_comm), rms in zip(classified, rms_vals):
        dur   = end - start
        label = "COMMERCIAL ✂" if is_comm else "content"
        print(f"{fmt_time(start):>8}  {fmt_time(end):>8}  "
              f"{dur:>5.0f}s  {rms:>5.1f}  {label}")
        if is_comm:
            total_commercial += dur
        else:
            total_content += dur

    total = total_content + total_commercial
    print("─" * 46)
    print(f"  Content    : {fmt_time(total_content)}  "
          f"({100*total_content/total:.0f}% of recording)")
    print(f"  Commercial : {fmt_time(total_commercial)}  "
          f"({100*total_commercial/total:.0f}% of recording)")
    print()


def stitch(input_path: Path,
           classified: list[tuple[float, float, bool]],
           output_path: Path) -> None:
    """Re-stitch content segments using ffmpeg concat demuxer (stream copy)."""
    content = [(s, e) for s, e, c in classified if not c]
    if not content:
        print("ERROR: No content segments identified — aborting to avoid empty output.",
              file=sys.stderr)
        sys.exit(1)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     delete=False) as f:
        list_path = Path(f.name)
        for start, end in content:
            f.write(f"file '{input_path.absolute()}'\n")
            f.write(f"inpoint {start:.3f}\n")
            f.write(f"outpoint {end:.3f}\n")

    try:
        subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0",
             "-i", str(list_path),
             "-c", "copy", "-y", str(output_path)],
            check=True,
        )
    finally:
        list_path.unlink(missing_ok=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strip commercial breaks from KTCK recordings.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input",          type=Path,
                        help="Input .aac recording")
    parser.add_argument("--output",       type=Path, default=None,
                        help="Output path (default: <input>_clean.aac)")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Analyse and print segment report without writing output")
    parser.add_argument("--silence-db",   type=float, default=SILENCE_DB,
                        help="Silence threshold in dBFS")
    parser.add_argument("--min-break",    type=float, default=MIN_COMMERCIAL,
                        help="Minimum commercial block length (seconds)")
    parser.add_argument("--max-break",    type=float, default=MAX_COMMERCIAL,
                        help="Maximum commercial block length (seconds)")
    parser.add_argument("--threshold",    type=float, default=LUFS_THRESHOLD,
                        help="dB below baseline RMS to classify as commercial")
    parser.add_argument("--max-spot",     type=float, default=MAX_SPOT_DUR,
                        help="Max duration of an individual spot sub-segment (seconds)")
    parser.add_argument("--max-intra-gap",type=float, default=MAX_INTRA_GAP,
                        help="Max silence gap inside a commercial break (seconds)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    output = args.output or args.input.with_name(
        args.input.stem + "_clean" + args.input.suffix
    )

    print(f"Input  : {args.input}  ({args.input.stat().st_size/1024/1024:.1f} MB)")
    print(f"Output : {output}")
    print(f"Params : silence={args.silence_db}dB  "
          f"break={args.min_break:.0f}–{args.max_break:.0f}s  "
          f"threshold=−{args.threshold}dB")

    # ── Step 1: silence detection ─────────────────────────────────────────────
    print("\nStep 1/3  Detecting silence boundaries…", end="", flush=True)
    total    = get_duration(args.input)
    silences = detect_silences(args.input, args.silence_db, SILENCE_DURATION)
    segments = silences_to_segments(silences, total)
    print(f"  {len(segments)} segments found  (total {fmt_time(total)})")

    # ── Step 2: measure RMS of each segment ───────────────────────────────────
    print(f"Step 2/3  Measuring loudness of {len(segments)} segments…",
          end="", flush=True)
    rms_vals = []
    for i, (start, end) in enumerate(segments):
        rms_vals.append(measure_rms(args.input, start, end))
        print(f"\r  Measuring segment {i+1}/{len(segments)}…  ", end="", flush=True)
    print()

    # ── Step 3: classify and report ────────────────────────────────────────────────
    print("Step 3/3  Classifying segments…")
    classified = cluster_commercials(
        segments, rms_vals,
        max_spot=args.max_spot,
        max_gap=args.max_intra_gap,
        min_comm=args.min_break,
        max_comm=args.max_break,
        lufs_threshold=args.threshold,
    )

    # Per-zone RMS for the report: average the original segment RMS values
    seg_rms_map = {(s, e): r for (s, e), r in zip(segments, rms_vals)}
    orig_seg_starts = {s: r for (s, e), r in zip(segments, rms_vals)}
    merged_rms = [orig_seg_starts.get(s, -99.0) for s, e, _ in classified]

    print_report(classified, merged_rms)

    if args.dry_run:
        print("Dry run — no output written.")
        return

    # ── Stitch ────────────────────────────────────────────────────────────────
    print(f"Stitching content segments → {output.name}…")
    stitch(args.input, classified, output)

    in_mb  = args.input.stat().st_size  / 1024 / 1024
    out_mb = output.stat().st_size      / 1024 / 1024
    saved  = 100 * (1 - out_mb / in_mb)
    print(f"\nDone.  {in_mb:.1f} MB → {out_mb:.1f} MB  ({saved:.0f}% removed)")


if __name__ == "__main__":
    main()
