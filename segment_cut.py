#!/usr/bin/env python3
"""
segment_cut.py — Find a recurring segment in full show recordings by its opening
audio ("sting") and cut it out automatically.

How it works
  1. Decode the recording and sting(s) to 8 kHz mono (ffmpeg).
  2. Turn each into a coarse spectrogram (40 log-spaced bands, 32 ms frames).
  3. Slide the start sting along the recording (normalized correlation via FFT)
     and pick the spots where it matches.
  4a. If an end sting is defined: pair each start match with the next end match.
  4b. If no end sting: cut a fixed duration (cut_minutes) from the start match.
  5. Cut with ffmpeg (no re-encode) into the show folder, named the way
     segment_post.py expects:  <Name> YYYYMMDD.mp3

Usage
  python3 segment_cut.py --dry-run                     # WTDS in all Hardline recordings, show cut times
  python3 segment_cut.py                               # cut them
  python3 segment_cut.py --segment ebrake              # cut Ebrake from all Musers recordings
  python3 segment_cut.py ~/Dropbox/TheTicket/"The Hardline 20261111.mp3" --dry-run
  python3 segment_cut.py --segment wtds --force        # overwrite existing cuts

Add a new segment by putting its start sting in stings/ and adding an entry to SEGMENTS.
An end sting is optional — omit it (set to None) to cut a fixed cut_minutes instead.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TICKET = Path("~/Dropbox/The Ticket").expanduser()   # segment output (organized by show)
RECORDINGS = Path("~/Dropbox/TheTicket").expanduser()  # full episode recordings from VPS

SR = 8000          # analysis sample rate
WIN = 1024         # FFT window (128 ms)
HOP = 256          # hop (32 ms per frame)
N_BANDS = 40
FMIN, FMAX = 100.0, 3800.0

SEGMENTS: dict[str, dict] = {
    "wtds": {
        "name": "WTDS",
        "show": "The Hardline",            # folder for output + which recordings to scan
        "start": HERE / "stings" / "wtds_start.mp3",
        "end":   HERE / "stings" / "wtds_end.mp3",
        "min_minutes": 3,
        "max_minutes": 45,
    },
    "ebrake": {
        "name": "Ebrake",
        "show": "The Musers",
        "start": HERE / "stings" / "ebrake_start.mp3",
        "end":   None,          # no consistent ending — cuts cut_minutes from start match
        "cut_minutes": 30,
        "min_minutes": 0,
        "max_minutes": 45,
    },
}


# ── Audio → features ───────────────────────────────────────────────────────────

def decode(path: Path) -> np.ndarray:
    """Decode any audio file to 8 kHz mono int16 via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16)


_edges = np.geomspace(FMIN, FMAX, N_BANDS + 1)
_freqs = np.fft.rfftfreq(WIN, 1 / SR)
_band_of_bin = np.digitize(_freqs, _edges) - 1  # -1 / N_BANDS = out of range
_window = np.hanning(WIN).astype(np.float32)


def features(x: np.ndarray, chunk_frames: int = 20000) -> np.ndarray:
    """(frames, N_BANDS) log band energies. Processed in chunks to bound memory."""
    n_frames = max(0, 1 + (len(x) - WIN) // HOP)
    out = np.empty((n_frames, N_BANDS), dtype=np.float32)
    valid = (_band_of_bin >= 0) & (_band_of_bin < N_BANDS)
    bins = _band_of_bin[valid]
    for f0 in range(0, n_frames, chunk_frames):
        f1 = min(n_frames, f0 + chunk_frames)
        idx = np.arange(f0, f1)[:, None] * HOP + np.arange(WIN)[None, :]
        frames = x[idx].astype(np.float32) * _window
        power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        bands = np.zeros((f1 - f0, N_BANDS), dtype=np.float32)
        np.add.at(bands.T, bins, power[:, valid].T)
        out[f0:f1] = np.log1p(bands)
    return out


def match_scores(sig: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    """Pearson correlation of the template against every window of the signal."""
    T, B = tpl.shape
    N = sig.shape[0]
    if N < T:
        return np.zeros(0, dtype=np.float32)
    tz = tpl - tpl.mean()
    tz /= np.linalg.norm(tz) + 1e-9
    n = 1 << int(np.ceil(np.log2(N + T)))
    num = np.zeros(N - T + 1, dtype=np.float64)
    for b in range(B):
        c = np.fft.irfft(np.fft.rfft(sig[:, b], n) * np.conj(np.fft.rfft(tz[:, b], n)), n)
        num += c[: N - T + 1]
    # window sums for the normalization term
    row = sig.sum(axis=1, dtype=np.float64)
    row2 = (sig.astype(np.float64) ** 2).sum(axis=1)
    cs = np.concatenate([[0.0], np.cumsum(row)])
    cs2 = np.concatenate([[0.0], np.cumsum(row2)])
    s = cs[T:] - cs[:-T]
    s2 = cs2[T:] - cs2[:-T]
    m = T * B
    var = np.maximum(s2 - s * s / m, 1e-9)
    return (num / np.sqrt(var)).astype(np.float32)


def peaks(scores: np.ndarray, width: int, threshold: float, limit: int = 20) -> list[tuple[int, float]]:
    """Greedy non-max suppression: best matches at least `width` frames apart."""
    s = scores.copy()
    out = []
    while len(out) < limit and s.size:
        i = int(np.argmax(s))
        if s[i] < threshold:
            break
        out.append((i, float(s[i])))
        s[max(0, i - width): i + width] = -np.inf
    return sorted(out)


# ── Segment detection ──────────────────────────────────────────────────────────

@dataclass
class Cut:
    start: float
    end: float
    start_score: float
    end_score: float


def fmt(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def find_cuts(recording: Path, seg: dict, threshold: float, verbose: bool) -> list[Cut]:
    start_f = features(decode(seg["start"]))
    sig = features(decode(recording))
    to_sec = HOP / SR

    s_scores = match_scores(sig, start_f)
    starts = peaks(s_scores, len(start_f), threshold)

    # End sting matching (optional)
    has_end = seg.get("end") is not None
    if has_end:
        end_f = features(decode(seg["end"]))
        end_len = len(decode(seg["end"])) / SR
        e_scores = match_scores(sig, end_f)
        ends = peaks(e_scores, len(end_f), threshold)
    else:
        end_len = 0.0
        e_scores = None
        ends = []

    if verbose:
        best_s = float(s_scores.max()) if s_scores.size else 0
        print(f"   start sting: {len(starts)} match(es) ≥ {threshold}  (best {best_s:.2f})"
              + "".join(f"\n      {fmt(i * to_sec)}  score {sc:.2f}" for i, sc in starts))
        if has_end:
            best_e = float(e_scores.max()) if e_scores.size else 0
            print(f"   end sting:   {len(ends)} match(es) ≥ {threshold}  (best {best_e:.2f})"
                  + "".join(f"\n      {fmt(i * to_sec + end_len)}  score {sc:.2f}" for i, sc in ends))
        else:
            print(f"   end sting:   none — cutting {seg['cut_minutes']} min from each start match")

    cuts: list[Cut] = []
    lo, hi = seg["min_minutes"] * 60, seg["max_minutes"] * 60

    if has_end:
        # Pair each start with the next valid end match
        used: set[int] = set()
        for si, ss in starts:
            t0 = si * to_sec
            for k, (ei, es) in enumerate(ends):
                t1 = ei * to_sec + end_len
                if k not in used and lo <= t1 - t0 <= hi:
                    cuts.append(Cut(t0, t1, ss, es))
                    used.add(k)
                    break
    else:
        # Fixed duration from start match
        dur = seg["cut_minutes"] * 60
        for si, ss in starts:
            t0 = si * to_sec
            cuts.append(Cut(t0, t0 + dur, ss, 0.0))

    return cuts


def recording_date(path: Path) -> str | None:
    m = re.search(r"(20\d{6})", path.stem) or re.search(r"(20\d\d)-(\d\d)-(\d\d)", path.stem)
    if not m:
        return None
    return "".join(m.groups()) if len(m.groups()) > 1 else m.group(1)


def cut_audio(src: Path, dst: Path, start: float, end: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src),
           "-t", f"{end - start:.3f}", "-c", "copy", str(dst)]
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="recordings (default: all matching show recordings)")
    ap.add_argument("--segment", default="wtds", choices=sorted(SEGMENTS))
    ap.add_argument("--threshold", type=float, default=0.6, help="match score 0–1 (default 0.6)")
    ap.add_argument("--dry-run", "-n", action="store_true", help="show detected cut times; write nothing")
    ap.add_argument("--force", action="store_true", help="overwrite existing output files")
    ap.add_argument("--outdir", type=Path, help="output folder (default ~/TheTicket/<show>)")
    args = ap.parse_args()

    seg = SEGMENTS[args.segment]
    if not Path(seg["start"]).exists():
        sys.exit(f"Missing sting: {seg['start']}")
    if seg.get("end") and not Path(seg["end"]).exists():
        sys.exit(f"Missing sting: {seg['end']}")
    files = args.files or sorted(RECORDINGS.glob(f"{seg['show']} *.mp3"))
    if not files:
        sys.exit(f"No recordings found for '{seg['show']}' in {RECORDINGS}")
    outdir = args.outdir or TICKET / seg["show"]

    made = skipped = missed = 0
    for rec in files:
        rec = rec.expanduser()
        print(f"\n{rec.name}", flush=True)
        cuts = find_cuts(rec, seg, args.threshold, verbose=args.dry_run)
        if not cuts:
            print("   ✗ no complete segment found (try --dry-run to see match scores)")
            missed += 1
            continue
        date = recording_date(rec) or "undated"
        for n, c in enumerate(cuts, 1):
            suffix = "" if len(cuts) == 1 else f" {n}"
            dst = outdir / f"{seg['name']} {date}{suffix}.mp3"
            line = (f"   {seg['name']}: {fmt(c.start)} → {fmt(c.end)}  ({(c.end - c.start) / 60:.1f} min, "
                    f"scores {c.start_score:.2f}/{c.end_score:.2f})  → {dst.name}")
            if args.dry_run:
                print(line + ("  [exists]" if dst.exists() else ""))
            elif dst.exists() and not args.force:
                print(line + "  — exists, skipped (use --force)")
                skipped += 1
            else:
                cut_audio(rec, dst, c.start, c.end)
                print(line + "  ✓")
                made += 1

    if not args.dry_run:
        print(f"\nDone: {made} cut, {skipped} skipped (already existed), {missed} recording(s) with no match.")


if __name__ == "__main__":
    main()
