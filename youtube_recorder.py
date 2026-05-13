#!/usr/bin/env python3
"""
YouTube Audio Recorder
======================
Downloads or records YouTube audio (as MP3) using yt-dlp.
General-purpose — works for any YouTube video or live stream.

Requirements:
    Python 3.9+
    yt-dlp  :  pip install yt-dlp
    ffmpeg  :  brew install ffmpeg

Usage:
    # Download audio from a video
    python3 youtube_recorder.py https://youtube.com/watch?v=...

    # Place output in a named subfolder
    python3 youtube_recorder.py https://youtube.com/watch?v=... --folder "Show Themes"

    # Group by channel name automatically
    python3 youtube_recorder.py https://youtube.com/watch?v=... --by-channel

    # Record a live stream until it ends
    python3 youtube_recorder.py https://youtube.com/watch?v=... --live

    # Custom output directory
    python3 youtube_recorder.py https://youtube.com/watch?v=... --output-dir ~/Music

    # Dry-run: show what would happen without downloading
    python3 youtube_recorder.py https://youtube.com/watch?v=... --dry-run
"""

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# ── Defaults ──────────────────────────────────────────────────────────────────

TIMEZONE           = ZoneInfo("America/Chicago")
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "YouTube Audio"
YTDLP_BIN          = "yt-dlp"   # must be on PATH  (pip install yt-dlp)
FFMPEG_BIN         = "ffmpeg"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("yt_recorder")

# ── Helpers ───────────────────────────────────────────────────────────────────


def _check_deps() -> None:
    """Abort early with a helpful message if yt-dlp or ffmpeg are missing."""
    missing = [b for b in (YTDLP_BIN, FFMPEG_BIN) if not shutil.which(b)]
    if missing:
        log.error(
            "Missing required tool(s): %s\n"
            "  Install with:  pip install yt-dlp   (for yt-dlp)\n"
            "                 brew install ffmpeg   (for ffmpeg)",
            ", ".join(missing),
        )
        sys.exit(1)


def _safe_filename(text: str) -> str:
    """Strip filesystem-unsafe characters; replace spaces with underscores."""
    return re.sub(r"[^\w\s\-]", "", text).strip().replace(" ", "_")


def _notify(title: str, message: str) -> None:
    """Send a macOS notification (no-op on other platforms)."""
    try:
        import platform
        if platform.system() == "Darwin":
            subprocess.run(
                [
                    "osascript", "-e",
                    f'display notification "{message}" '
                    f'with title "YouTube Recorder" '
                    f'subtitle "{title}" sound name "Glass"',
                ],
                check=False,
                capture_output=True,
            )
    except Exception:
        pass


def _fetch_metadata(url: str) -> dict:
    """Return the yt-dlp JSON metadata for a URL (no download)."""
    result = subprocess.run(
        [YTDLP_BIN, "--dump-json", "--no-playlist", url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("yt-dlp metadata fetch failed:\n%s", result.stderr.strip())
        sys.exit(1)
    return json.loads(result.stdout)


# ── Core download / record ────────────────────────────────────────────────────


def download_audio(
    url: str,
    output_dir: Path,
    folder: Optional[str] = None,
    by_channel: bool = False,
    live: bool = False,
    dry_run: bool = False,
) -> Optional[Path]:
    """
    Download or record YouTube audio as MP3.

    Parameters
    ----------
    url        : YouTube video or live-stream URL
    output_dir : Root output directory
    folder     : Place output in this named subfolder (overrides by_channel)
    by_channel : Automatically create a subfolder named after the channel
    live       : If True, pass live-stream flags to yt-dlp
    dry_run    : Log what would happen but don't download anything

    Returns
    -------
    Path of the output MP3 file, or None on dry-run / failure.
    """
    _check_deps()

    # ── Fetch metadata ────────────────────────────────────────────────────────
    log.info("Fetching metadata for: %s", url)
    meta      = _fetch_metadata(url)
    raw_title = meta.get("title", "Unknown")
    channel   = meta.get("channel") or meta.get("uploader") or "Unknown Channel"
    upload_dt = None

    # Prefer the upload date from metadata; fall back to now
    upload_date_str = meta.get("upload_date")  # "YYYYMMDD"
    if upload_date_str:
        try:
            upload_dt = datetime.strptime(upload_date_str, "%Y%m%d").replace(
                tzinfo=TIMEZONE
            )
        except ValueError:
            pass
    if upload_dt is None:
        upload_dt = datetime.now(TIMEZONE)

    # ── Resolve output path ───────────────────────────────────────────────────
    if folder:
        dest_dir = output_dir / folder
    elif by_channel:
        dest_dir = output_dir / _safe_filename(channel).replace("_", " ")
    else:
        dest_dir = output_dir

    date_stamp = upload_dt.strftime("%Y-%m-%d_%H%M")
    safe_title = _safe_filename(raw_title)
    out_name   = f"{date_stamp}_{safe_title}.mp3"
    out_path   = dest_dir / out_name

    log.info("Video title  : %s", raw_title)
    log.info("Channel      : %s", channel)
    log.info("Output file  : %s", out_path)

    if dry_run:
        log.info("DRY RUN — no file will be downloaded.")
        return None

    # ── Create output directory ───────────────────────────────────────────────
    dest_dir.mkdir(parents=True, exist_ok=True)

    # ── Build yt-dlp command ──────────────────────────────────────────────────
    # -x                 : extract audio only (no video downloaded)
    # --audio-format mp3 : re-encode to MP3
    # --audio-quality 0  : best VBR quality
    # -o                 : output template
    cmd = [
        YTDLP_BIN,
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "-o", str(dest_dir / f"{date_stamp}_{safe_title}.%(ext)s"),
    ]

    if live:
        # For live streams, yt-dlp should keep downloading until the stream ends
        cmd += [
            "--live-from-start",    # start from beginning of the live DVR window
            "--no-part",            # write directly to the final file
        ]

    cmd.append(url)

    log.info("Starting download%s…", " (live stream)" if live else "")
    log.info("Command: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        log.error("yt-dlp exited with code %d", exc.returncode)
        _notify("Download failed", f"yt-dlp error for: {raw_title}")
        return None
    except KeyboardInterrupt:
        log.info("Interrupted — partial file may exist at %s", out_path)
        return None

    # ── Verify output ─────────────────────────────────────────────────────────
    if out_path.exists():
        size_mb = out_path.stat().st_size / 1_048_576
        log.info("✔  Saved: %s  (%.1f MB)", out_path.name, size_mb)
        _notify("Download complete", out_path.name)
        return out_path
    else:
        # yt-dlp may produce a slightly different filename; find it
        candidates = sorted(dest_dir.glob(f"{date_stamp}_{safe_title}*.mp3"))
        if candidates:
            actual = candidates[0]
            size_mb = actual.stat().st_size / 1_048_576
            log.info("✔  Saved: %s  (%.1f MB)", actual.name, size_mb)
            _notify("Download complete", actual.name)
            return actual

        log.warning("Download finished but output file not found at expected path.")
        log.warning("Check %s for the downloaded file.", dest_dir)
        return None


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download or record YouTube audio as MP3 into a show subfolder.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "url",
        help="YouTube video or live-stream URL",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        metavar="DIR",
        help="Root Dropbox/output directory; a show subfolder is created inside it",
    )
    parser.add_argument(
        "--folder",
        metavar="NAME",
        default=None,
        help='Place output in a named subfolder, e.g. "Show Themes"',
    )
    parser.add_argument(
        "--by-channel",
        action="store_true",
        help="Automatically create a subfolder named after the YouTube channel",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Live-stream mode — record until the stream ends",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch metadata and log the plan; do not download",
    )
    args = parser.parse_args()

    download_audio(
        url=args.url,
        output_dir=args.output_dir,
        folder=args.folder,
        by_channel=args.by_channel,
        live=args.live,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
