#!/usr/bin/env python3
"""
transistor_post.py — Convert completed recordings to MP3 and post to Transistor.fm
====================================================================================
Scans a directory for finished .aac recordings, converts each to MP3 (deleting
the source AAC after verification), then publishes to Transistor.fm.

Requirements:
    TRANSISTOR_API_KEY environment variable (or --api-key flag)
    ffmpeg on PATH

Usage:
    python3 transistor_post.py --dir ~/recordings
    python3 transistor_post.py --dir ~/recordings --dry-run
    python3 transistor_post.py --file recording.mp3   # post a single pre-converted file

Episode title format: "Show Name - MM.DD.YYYY" (Transistor); filename: "Show Name YYYYMMDD.mp3"
Show name on Transistor: "The Ticket"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

API_BASE    = "https://api.transistor.fm/v1"
SHOW_TITLE  = "The Ticket"
STATE_FILE  = Path(__file__).parent / "transistor_uploaded.json"
LOG_FILE    = Path(__file__).parent / "logs" / "transistor.log"
STALE_SECS  = 120   # skip files modified within last N seconds (still recording)
RATE_SLEEP  = 1.2   # seconds between Transistor API calls (limit: 10/10s)
RETRY_WAIT  = 12    # seconds to wait after a 429 response

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("transistor_post")

# ── State (local dedup cache) ─────────────────────────────────────────────────


def load_state() -> dict:
    """Return {filename: episode_id} from local state file."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── API helpers ────────────────────────────────────────────────────────────────


def _request(method: str, url: str, api_key: str,
             data: Optional[bytes] = None,
             content_type: str = "application/x-www-form-urlencoded",
             extra_headers: Optional[dict] = None) -> dict:
    """Make an authenticated Transistor API request, retrying on 429 and network errors."""
    headers = {"x-api-key": api_key, "Content-Type": content_type}
    if extra_headers:
        headers.update(extra_headers)
    for attempt in range(5):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = RETRY_WAIT * (attempt + 1)
                log.warning("Rate limited — waiting %ds before retry %d", wait, attempt + 1)
                time.sleep(wait)
                continue
            body = exc.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {body}") from exc
        except OSError as exc:
            if attempt < 4:
                wait = 10 * (attempt + 1)
                log.warning("Network error (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Network error after 5 attempts: {exc}") from exc
    raise RuntimeError("Max retries exceeded")


def api_get(path: str, api_key: str, params: Optional[dict] = None) -> dict:
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    time.sleep(RATE_SLEEP)
    return _request("GET", url, api_key)


def api_post(path: str, api_key: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    time.sleep(RATE_SLEEP)
    return _request("POST", API_BASE + path, api_key, data=data)


def api_patch(path: str, api_key: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    time.sleep(RATE_SLEEP)
    return _request("PATCH", API_BASE + path, api_key, data=data)


def upload_file_to_s3(upload_url: str, file_path: Path) -> None:
    """PUT the MP3 directly to the S3 presigned URL (no Transistor auth needed)."""
    log.info("  Uploading %s (%s MB) …",
             file_path.name, f"{file_path.stat().st_size / 1_048_576:.1f}")
    with file_path.open("rb") as fh:
        data = fh.read()
    req = urllib.request.Request(upload_url, data=data, method="PUT")
    req.add_header("Content-Type", "audio/mpeg")
    with urllib.request.urlopen(req, timeout=300) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"S3 upload failed with status {resp.status}")


# ── Transistor show lookup ─────────────────────────────────────────────────────


def find_show_id(api_key: str) -> str:
    """Return the Transistor show ID for SHOW_TITLE, loading all pages."""
    page = 1
    while True:
        resp = api_get("/shows", api_key, {"pagination[page]": page, "pagination[per]": 20})
        for show in resp.get("data", []):
            if show.get("attributes", {}).get("title", "").strip() == SHOW_TITLE:
                return show["id"]
        meta = resp.get("meta", {})
        if page >= meta.get("totalPages", 1):
            break
        page += 1
    raise RuntimeError(f"Could not find show titled '{SHOW_TITLE}' on Transistor")


def load_existing_titles(show_id: str, api_key: str) -> set[str]:
    """Load all episode titles already posted to Transistor (all pages)."""
    titles: set[str] = set()
    page = 1
    while True:
        resp = api_get("/episodes", api_key, {
            "show_id": show_id,
            "pagination[page]": page,
            "pagination[per]": 50,
        })
        episodes = resp.get("data", [])
        for ep in episodes:
            t = ep.get("attributes", {}).get("title", "").strip()
            if t:
                titles.add(t)
        meta = resp.get("meta", {})
        if page >= meta.get("totalPages", 1):
            break
        page += 1
    log.info("Loaded %d existing episode titles from Transistor", len(titles))
    return titles


# ── Filename parsing ───────────────────────────────────────────────────────────

# Raw recorder format:  2026-05-22_0600_The_Musers
_FNAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_\d{4}_(.+)$")
# Renamed canonical format: The Musers 20260522
_RENAMED_RE = re.compile(r"^(.+?)\s+(\d{4})(\d{2})(\d{2})$")


def parse_recording(path: Path) -> tuple[Optional[str], Optional[datetime]]:
    """
    Parse both filename formats into (show_title, date):
      - Raw:     YYYY-MM-DD_HHMM_Show_Name.{aac,mp3}
      - Renamed: Show Name YYYYMMDD.mp3
    Returns (None, None) if neither matches.
    """
    m = _FNAME_RE.match(path.stem)
    if m:
        date_obj = datetime.strptime(m.group(1), "%Y-%m-%d")
        show_title = m.group(2).replace("_", " ")
        return show_title, date_obj
    m = _RENAMED_RE.match(path.stem)
    if m:
        show_title = m.group(1)
        date_obj = datetime.strptime(f"{m.group(2)}-{m.group(3)}-{m.group(4)}", "%Y-%m-%d")
        return show_title, date_obj
    return None, None


def format_title(show_title: str, date_obj: datetime) -> str:
    return f"{show_title} - {date_obj.strftime('%m.%d.%Y')}"


# ── File readiness ─────────────────────────────────────────────────────────────


def is_being_written(path: Path) -> bool:
    """True if any process has the file open (i.e. still recording)."""
    try:
        result = subprocess.run(
            ["lsof", "-F", "n", str(path)],
            capture_output=True, text=True
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def is_stale(path: Path) -> bool:
    """True if file hasn't been modified in the last STALE_SECS seconds."""
    age = time.time() - path.stat().st_mtime
    return age >= STALE_SECS


# ── AAC → MP3 conversion ───────────────────────────────────────────────────────


def convert_to_mp3(aac_path: Path) -> Optional[Path]:
    """
    Convert an AAC file to MP3 at 128 kbps. Verifies output with ffprobe.
    Deletes the source AAC only after successful verification.
    Returns the MP3 path, or None on failure.
    """
    mp3_path = aac_path.with_suffix(".mp3")
    log.info("  Converting %s → %s", aac_path.name, mp3_path.name)
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(aac_path),
         "-codec:a", "libmp3lame", "-b:a", "128k",
         str(mp3_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("  ffmpeg failed: %s", result.stderr.strip())
        mp3_path.unlink(missing_ok=True)
        return None

    # Verify
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(mp3_path)],
        capture_output=True, text=True,
    )
    try:
        duration = float(probe.stdout.strip())
        if duration < 1:
            raise ValueError("duration < 1s")
    except (ValueError, TypeError):
        log.error("  MP3 verify failed — empty or unreadable output")
        mp3_path.unlink(missing_ok=True)
        return None

    size_mb = mp3_path.stat().st_size / 1_048_576
    log.info("  Converted: %.1f MB, %s", size_mb,
             time.strftime("%H:%M:%S", time.gmtime(duration)))

    aac_path.unlink()
    log.info("  Deleted source AAC: %s", aac_path.name)
    return mp3_path


# ── Transistor posting ─────────────────────────────────────────────────────────


def post_episode(mp3_path: Path, show_id: str, api_key: str,
                 title: str, date_obj: datetime) -> str:
    """
    Upload mp3_path to Transistor and publish it.
    Returns the episode ID.
    """
    # Step 1: authorize upload
    auth = api_get(
        "/episodes/authorize_upload", api_key,
        {"filename": mp3_path.name}
    )
    upload_url = auth["data"]["attributes"]["upload_url"]
    audio_url  = auth["data"]["attributes"]["audio_url"]

    # Step 2: upload file to S3
    upload_file_to_s3(upload_url, mp3_path)

    # Step 3: create episode (draft), then publish with date in a separate PATCH
    # (Transistor rejects status + published_at in the initial POST)
    published_at = date_obj.strftime("%Y-%m-%dT12:00:00+00:00")
    ep_resp = api_post("/episodes", api_key, {
        "episode[show_id]":   show_id,
        "episode[title]":     title,
        "episode[audio_url]": audio_url,
    })
    ep_id = ep_resp["data"]["id"]
    log.info("  Created episode %s — setting date %s", ep_id, published_at)
    # Set the publish date first (separate from status change)
    api_patch(f"/episodes/{ep_id}", api_key, {
        "episode[published_at]": published_at,
    })
    # Then publish via the dedicated publish endpoint
    api_patch(f"/episodes/{ep_id}/publish", api_key, {"episode[status]": "published"})

    # Verify: GET the episode to confirm Transistor actually stored it as published
    verify_resp = api_get(f"/episodes/{ep_id}", api_key)
    verified_status = verify_resp.get("data", {}).get("attributes", {}).get("status", "unknown")
    if verified_status != "published":
        log.warning("  Verification failed — episode %s is '%s', forcing publish", ep_id, verified_status)
        api_patch(f"/episodes/{ep_id}/publish", api_key, {"episode[status]": "published"})
        # Final check
        recheck = api_get(f"/episodes/{ep_id}", api_key)
        final_status = recheck.get("data", {}).get("attributes", {}).get("status", "unknown")
        log.warning("  Post-force status: %s", final_status)
    else:
        log.info("  Verified published: %s (ID %s)", title, ep_id)

    return ep_id


# ── Main loop ──────────────────────────────────────────────────────────────────


def process_directory(recording_dir: Path, show_id: str, api_key: str,
                      existing_titles: set[str], state: dict,
                      dry_run: bool) -> None:
    """Scan directory and process any finished recordings."""
    candidates = sorted(recording_dir.glob("*.aac")) + sorted(recording_dir.glob("*.mp3"))
    posted = 0
    skipped = 0

    for path in candidates:
        show_title, date_obj = parse_recording(path)
        if show_title is None:
            log.debug("  Skipping unrecognized filename: %s", path.name)
            continue

        title = format_title(show_title, date_obj)

        # Skip if already uploaded (local state)
        if path.stem in state:
            log.debug("  Already posted (state): %s", title)
            skipped += 1
            continue

        # Skip if title already on Transistor
        if title.strip() in existing_titles:
            log.info("  Already on Transistor — skipping: %s", title)
            state[path.stem] = "existing"
            save_state(state)
            skipped += 1
            continue

        # Skip if file is still being written
        if is_being_written(path):
            log.info("  Still recording — skipping: %s", path.name)
            continue

        # Skip if modified too recently
        if not is_stale(path):
            log.info("  Too recent — skipping: %s", path.name)
            continue

        log.info("Processing: %s → '%s'", path.name, title)

        if dry_run:
            log.info("  DRY-RUN: would convert and post '%s'", title)
            continue

        # Convert AAC → MP3 if needed
        mp3_path = path
        if path.suffix.lower() == ".aac":
            mp3_path = convert_to_mp3(path)
            if mp3_path is None:
                log.error("  Conversion failed — skipping: %s", path.name)
                continue
            # Rename to canonical title format: "Show Title YYYYMMDD.mp3"
            renamed = mp3_path.parent / f"{show_title} {date_obj.strftime('%Y%m%d')}.mp3"
            if renamed != mp3_path and not renamed.exists():
                mp3_path.rename(renamed)
                log.info("  Renamed: %s → %s", mp3_path.name, renamed.name)
                mp3_path = renamed

        # Post to Transistor
        try:
            ep_id = post_episode(mp3_path, show_id, api_key, title, date_obj)
            # Rename to canonical format after a successful post (only raw-named files)
            if path.suffix.lower() != ".aac" and _FNAME_RE.match(mp3_path.stem):
                renamed = mp3_path.parent / f"{show_title} {date_obj.strftime('%Y%m%d')}.mp3"
                if renamed != mp3_path and not renamed.exists():
                    mp3_path.rename(renamed)
                    log.info("  Renamed: %s → %s", mp3_path.name, renamed.name)
            state[mp3_path.stem] = ep_id
            existing_titles.add(title.strip())
            save_state(state)
            posted += 1
        except Exception as exc:
            log.error("  Failed to post '%s': %s", title, exc)

    log.info("Done — %d posted, %d skipped", posted, skipped)


def process_single_file(mp3_path: Path, show_id: str, api_key: str,
                        existing_titles: set[str], state: dict,
                        dry_run: bool) -> None:
    """Post a single pre-converted MP3 file."""
    show_title, date_obj = parse_recording(mp3_path)
    if show_title is None:
        log.error("Cannot parse filename: %s", mp3_path.name)
        sys.exit(1)

    title = format_title(show_title, date_obj)

    if mp3_path.stem in state:
        log.info("Already posted (state): %s", title)
        return

    if title.strip() in existing_titles:
        log.info("Already on Transistor: %s", title)
        return

    log.info("Posting: '%s'", title)
    if dry_run:
        log.info("DRY-RUN — would post '%s'", title)
        return

    ep_id = post_episode(mp3_path, show_id, api_key, title, date_obj)
    state[mp3_path.stem] = ep_id
    save_state(state)


# ── CLI ────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert finished recordings to MP3 and post to Transistor.fm."
    )
    parser.add_argument("--dir", type=Path, metavar="DIR",
                        help="Directory to scan for recordings")
    parser.add_argument("--file", type=Path, metavar="FILE",
                        help="Post a single MP3 file (skip conversion)")
    parser.add_argument("--api-key", default=os.environ.get("TRANSISTOR_API_KEY"),
                        help="Transistor API key (default: $TRANSISTOR_API_KEY)")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Preview without converting or posting")
    args = parser.parse_args()

    if not args.api_key:
        log.error("No API key — set TRANSISTOR_API_KEY or pass --api-key")
        sys.exit(1)

    if not args.dir and not args.file:
        parser.error("Provide --dir or --file")

    # Resolve Transistor show
    log.info("Looking up show '%s' on Transistor …", SHOW_TITLE)
    show_id = find_show_id(args.api_key)
    log.info("Found show ID: %s", show_id)

    # Load existing titles (dedup)
    existing_titles = load_existing_titles(show_id, args.api_key)

    # Load local state
    state = load_state()

    if args.file:
        process_single_file(args.file.expanduser(), show_id, args.api_key,
                            existing_titles, state, args.dry_run)
    else:
        process_directory(args.dir.expanduser(), show_id, args.api_key,
                          existing_titles, state, args.dry_run)


if __name__ == "__main__":
    main()
