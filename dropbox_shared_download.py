#!/usr/bin/env python3
"""
dropbox_shared_download.py — Download all files from a public Dropbox shared folder.

Reads the OAuth token from the rclone config, lists all files in the shared
folder via the Dropbox API, and downloads each one. If the filename contains
a month/day but no year, the year is appended from the file's server_modified date.

Usage:
    python3 dropbox_shared_download.py --url <shared_link> --out <dir>
    python3 dropbox_shared_download.py --url <shared_link> --out <dir> --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dbx_download")

# ── Dropbox API ───────────────────────────────────────────────────────────────

API_BASE     = "https://api.dropboxapi.com/2"
CONTENT_BASE = "https://content.dropboxapi.com/2"
RATE_SLEEP   = 0.5


def get_token_from_rclone(rclone_conf: Path | None = None) -> str:
    """Extract the Dropbox access token from the rclone config file."""
    candidates = [
        rclone_conf,
        Path.home() / ".config" / "rclone" / "rclone.conf",
        Path("/home/ticket/.config/rclone/rclone.conf"),
    ]
    for path in candidates:
        if path and path.exists():
            text = path.read_text()
            # Find the [dropbox] section and extract token JSON
            in_section = False
            for line in text.splitlines():
                if line.strip().lower() == "[dropbox]":
                    in_section = True
                    continue
                if in_section and line.startswith("["):
                    break
                if in_section and line.startswith("token"):
                    token_json = line.split("=", 1)[1].strip()
                    token_data = json.loads(token_json)
                    return token_data["access_token"]
    raise RuntimeError("Could not find Dropbox token in rclone config")


def api_post(endpoint: str, token: str, body: dict, base: str = API_BASE) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        base + endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    time.sleep(RATE_SLEEP)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def list_shared_folder(url: str, token: str) -> list[dict]:
    """Return all file entries in a shared Dropbox folder (handles pagination)."""
    entries = []
    body = {
        "path": "",
        "shared_link": {"url": url},
        "recursive": False,
        "include_media_info": False,
    }
    resp = api_post("/files/list_folder", token, body)
    entries.extend(resp.get("entries", []))

    while resp.get("has_more"):
        resp = api_post(
            "/files/list_folder/continue",
            token,
            {"cursor": resp["cursor"]},
        )
        entries.extend(resp.get("entries", []))

    return [e for e in entries if e.get(".tag") == "file"]


def clean_url(url: str) -> str:
    """Strip tracking params, keep only the rlkey which is needed for auth."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    clean_params = {k: v for k, v in params.items() if k in ("rlkey",)}
    clean_query = urllib.parse.urlencode({k: v[0] for k, v in clean_params.items()})
    return urllib.parse.urlunparse(parsed._replace(query=clean_query))


def download_file(shared_url: str, file_path: str, token: str, dest: Path) -> None:
    """Download a single file from the shared folder to dest."""
    url = clean_url(shared_url)
    arg = json.dumps({"url": url, "path": file_path})
    req = urllib.request.Request(
        CONTENT_BASE + "/sharing/get_shared_link_file",
        data=b"",
        headers={
            "Authorization": f"Bearer {token}",
            "Dropbox-API-Arg": arg,
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        dest.write_bytes(resp.read())


# ── Filename processing ───────────────────────────────────────────────────────

# Matches "Month Day" or "Month. Day" patterns without a 4-digit year nearby
_MONTH_DAY_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)[.\s]+(\d{1,2})\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def needs_year(name: str) -> bool:
    """True if name has a month/day pattern but no 4-digit year."""
    return bool(_MONTH_DAY_RE.search(name)) and not bool(_YEAR_RE.search(name))


def append_year(name: str, year: int) -> str:
    """Append the year before the file extension."""
    stem, _, ext = name.rpartition(".")
    return f"{stem} {year}.{ext}" if stem else f"{name} {year}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Download a public Dropbox shared folder.")
    parser.add_argument("--url",     required=True, help="Dropbox shared folder URL")
    parser.add_argument("--out",     required=True, type=Path, help="Destination directory")
    parser.add_argument("--dry-run", "-n", action="store_true")
    parser.add_argument("--rclone-conf", type=Path, default=None)
    args = parser.parse_args()

    token = get_token_from_rclone(args.rclone_conf)
    log.info("Token loaded from rclone config")

    args.out.mkdir(parents=True, exist_ok=True)

    log.info("Listing files in shared folder…")
    files = list_shared_folder(args.url, token)
    log.info("Found %d file(s)", len(files))

    downloaded = skipped = 0
    for entry in files:
        orig_name   = entry["name"]
        server_mod  = entry.get("server_modified", "")
        year        = int(server_mod[:4]) if server_mod else None

        # Determine output filename
        out_name = orig_name
        if year and needs_year(orig_name):
            out_name = append_year(orig_name, year)
            log.info("  %s → %s", orig_name, out_name)
        else:
            log.info("  %s", orig_name)

        dest = args.out / out_name
        if dest.exists():
            log.info("    SKIP (already exists)")
            skipped += 1
            continue

        if args.dry_run:
            log.info("    DRY-RUN")
            continue

        try:
            download_file(args.url, "/" + orig_name, token, dest)
            size_mb = dest.stat().st_size / 1_048_576
            log.info("    ✓  %.1f MB", size_mb)
            downloaded += 1
        except Exception as exc:
            log.error("    FAILED: %s", exc)

    log.info("Done — %d downloaded, %d skipped", downloaded, skipped)


if __name__ == "__main__":
    main()
