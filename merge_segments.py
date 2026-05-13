#!/usr/bin/env python3
"""
merge_segments.py — Join split recordings of the same show into one file.

When ffmpeg dies mid-show and the recorder restarts, the same show gets
two files with different timestamps:

  2026-05-13_0600_The_Musers.aac    ← before failure
  2026-05-13_0632_The_Musers.aac    ← resumed after failure

This script detects such groups (same date + show name, different start
times) and merges them in chronological order.  The merged file takes the
name of the earliest segment; the later fragments are deleted.

⚠  Do NOT run on recordings made with --segment-minutes: those intentional
   splits would be incorrectly merged into a single file.

Usage:
    python3 merge_segments.py ~/Dropbox/TheTicket
    python3 merge_segments.py ~/Dropbox/TheTicket --dry-run
"""

import argparse
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

FNAME_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})_(\d{4})_(.+?)(?:_\d+)?\.aac$')


def parse_name(f: Path):
    """Return (date, hhmm, show_slug) or None."""
    m = FNAME_RE.match(f.name)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def is_active(f: Path) -> bool:
    return bool(subprocess.run(['lsof', str(f)],
                               capture_output=True).stdout)


def fmt(n_bytes: int) -> str:
    for u in ('B', 'KB', 'MB', 'GB'):
        if n_bytes < 1024:
            return f'{n_bytes:.1f} {u}'
        n_bytes /= 1024
    return f'{n_bytes:.1f} TB'


def merge(files: list[Path], dry_run: bool) -> bool:
    """Merge files (sorted by name) into the first one.  Returns True on success."""
    files = sorted(files, key=lambda f: f.name)
    target = files[0]
    total  = sum(f.stat().st_size for f in files)

    if dry_run:
        print(f'\n  WOULD MERGE → {target.name}  ({fmt(total)})')
        for f in files:
            print(f'    + {f.name}  ({fmt(f.stat().st_size)})')
        return True

    tmp = target.with_suffix('.merging.aac')
    list_path = Path(tempfile.mktemp(suffix='.txt'))
    try:
        with open(list_path, 'w') as lf:
            for f in files:
                lf.write(f"file '{f.absolute()}'\n")

        r = subprocess.run(
            ['ffmpeg', '-hide_banner', '-loglevel', 'warning',
             '-f', 'concat', '-safe', '0',
             '-i', str(list_path), '-c', 'copy', '-y', str(tmp)],
            capture_output=True,
        )
        if r.returncode != 0:
            print(f'  ✘  ffmpeg failed: {r.stderr.decode()[:200]}',
                  file=sys.stderr)
            tmp.unlink(missing_ok=True)
            return False

        merged_size = tmp.stat().st_size
        tmp.rename(target)
        for f in files[1:]:
            f.unlink()

        print(f'\n  ✔  Merged {len(files)} segments → {target.name}')
        print(f'     {" + ".join(fmt(f.stat().st_size) for f in files[1:])} '
              f'absorbed  →  {fmt(merged_size)} total')
        return True

    finally:
        list_path.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description='Merge split same-show recordings from recorder restarts.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument('output_dir', type=Path,
                    help='Directory to scan')
    ap.add_argument('--dry-run', action='store_true',
                    help='Show what would be merged without touching files')
    args = ap.parse_args()

    if not args.output_dir.exists():
        print(f'Directory not found: {args.output_dir}', file=sys.stderr)
        sys.exit(1)

    # Group files by (date, show_slug) — different timestamps = resume candidates
    groups: dict[tuple, list[Path]] = defaultdict(list)
    for f in sorted(args.output_dir.glob('*.aac')):
        if f.name.endswith('_clean.aac'):
            continue
        parts = parse_name(f)
        if not parts:
            continue
        date, hhmm, show = parts
        groups[(date, show)].append(f)

    candidates = {
        key: files
        for key, files in groups.items()
        if len({parse_name(f)[1] for f in files}) > 1   # 2+ distinct timestamps
    }

    if not candidates:
        print('No split recordings found.')
        return

    print(f'Found {len(candidates)} show(s) with split segments:')
    if args.dry_run:
        print('DRY RUN — no files will be changed\n')

    merged = skipped = 0
    for (date, show), files in sorted(candidates.items()):
        active = [f for f in files if is_active(f)]
        if active:
            print(f'\n  SKIP (recording in progress): {date} {show}')
            skipped += 1
            continue

        if merge(files, args.dry_run):
            merged += 1
        else:
            skipped += 1

    print(f'\nDone: {merged} merged  {skipped} skipped')


if __name__ == '__main__':
    main()
