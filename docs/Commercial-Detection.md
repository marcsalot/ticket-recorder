# Commercial Detection Tuning Guide

`strip_commercials.py` uses two signals to detect commercial breaks in KTCK recordings.

## How it works

### Signal 1 — Fragmentation clustering

KTCK commercial breaks don't produce true silence. Instead they appear as a dense cluster of short silence-delimited sub-segments (individual spots + inter-spot jingles, 1–45s each). The algorithm:

1. Runs `ffmpeg silencedetect` to find all silence boundaries
2. Walks the segment list, greedily merging adjacent short segments (duration ≤ `--max-spot`, gap ≤ `--max-intra-gap`) into candidate zones
3. Applies a duration filter: zones between `--min-break` and `--max-break` seconds are candidates

### Signal 2 — RMS gate

On KTCK, show hosts are louder than most commercials. The zone's average RMS is compared against a 75th-percentile baseline derived from the long (clearly-content) segments. If `--threshold 0` is set, this gate is disabled and only clustering is used.

## Always dry-run first

```bash
python3 strip_commercials.py recording.aac --dry-run
```

Review the table. `COMMERCIAL ✂` rows show what would be cut. The original file is **never touched** until you run without `--dry-run`.

## Baseline parameters (verified on 2026-05-12)

| Show | Raw | Clean | Removed |
|---|---|---|---|
| The Invasion (~75 min) | 26 MB | 17 MB | 39% |
| The Sweet Spot (~100 min) | 42 MB | 29 MB | 34% |
| The Hardline (240 min) | 84 MB | 55 MB | 35% |

Expect **32–40%** removal on a normal weekday show with default parameters.

## All flags

| Flag | Default | Description |
|---|---|---|
| `--threshold N` | 3.0 | dB below baseline RMS to classify as commercial. Set to `0` to use clustering only (best for overnight/specials). |
| `--min-break N` | 60 | Minimum break duration in seconds. Raise if short segments of show content are being cut. |
| `--max-break N` | 420 | Maximum break duration in seconds. Raise if long commercial blocks are being missed. |
| `--max-spot N` | 60 | Max sub-segment length to include in a cluster. Lower if show content is being grouped into breaks. |
| `--max-intra-gap N` | 12 | Max silence gap inside a break. Raise if back-to-back spots are being split into separate breaks. |
| `--dry-run` | off | Preview only, write nothing. Always use this first. |

## Tuning for different scenarios

### Regular weekday show (default parameters)
```bash
python3 strip_commercials.py 2026-05-13_1500_The_Hardline.aac --dry-run
python3 strip_commercials.py 2026-05-13_1500_The_Hardline.aac
```

### Overnight special event (Campound, etc.)
Use `--threshold 0` — the overnight format has different energy levels and the RMS gate can misfire:
```bash
python3 strip_commercials.py 2026-05-13_1900_Campound_2026_Night_1.aac --dry-run --threshold 0
python3 strip_commercials.py 2026-05-13_1900_Campound_2026_Night_1.aac --threshold 0
```

### Missed breaks (too conservative)
```bash
# Try raising the max break window
python3 strip_commercials.py recording.aac --dry-run --max-break 540

# Or disable the RMS gate entirely
python3 strip_commercials.py recording.aac --dry-run --threshold 0
```

### False positives (cutting show content)
```bash
# Require longer minimum break
python3 strip_commercials.py recording.aac --dry-run --min-break 90

# Tighter RMS gate
python3 strip_commercials.py recording.aac --dry-run --threshold 5

# Smaller max spot size (harder to cluster)
python3 strip_commercials.py recording.aac --dry-run --max-spot 30
```

## Integrating with the processor

Once you've found parameters that work consistently, embed them in `process_recordings.sh`:

```bash
# Edit process_recordings.sh and add STRIP_ARGS to the launchd plist, or call manually:
bash ~/ticket-recorder/process_recordings.sh --threshold 0
```

## Output files

- Input: `2026-05-13_1500_The_Hardline.aac` (84 MB)
- Output: `2026-05-13_1500_The_Hardline_clean.aac` (55 MB)
- The original is **never deleted or modified**

After you're confident the detection is reliable, you can delete the originals and keep only the `_clean` files.
