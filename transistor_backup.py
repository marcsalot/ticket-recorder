#!/usr/bin/env python3
"""
transistor_backup.py — Preserve Transistor episodes by copying them to Google Drive.

For every episode on "The Ticket Archive" whose title matches --match (and not
--exclude), download the MP3 and upload it with rclone, plus a .txt sidecar with
the title, air/publish date, description, tags and Transistor IDs.

Files are named like the Dropbox archive:  YYYY-MM-DD_<Title>.mp3 / .txt
(the format segment_post.py already understands). Anything already present at
the destination is skipped, so the script is safe to re-run / resume.

Usage:
    python3 transistor_backup.py --dry-run                     # list what would be copied
    python3 transistor_backup.py                               # legacy Rants → gdrive:The Rant
    python3 transistor_backup.py --match "elmer wayne" --exclude "" --dest "gdrive:The Ticket/Elmer Wayne"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.transistor.fm/v1"
SHOW_NAME = "The Ticket Archive"
ENV_FILE = Path("~/.segment_post_env").expanduser()
RATE_SLEEP = 1.2  # Transistor allows ~10 req / 10 s


def api_key() -> str:
    key = os.environ.get("TRANSISTOR_API_KEY")
    if not key and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("TRANSISTOR_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("TRANSISTOR_API_KEY not found (env or ~/.segment_post_env)")
    return key


def api_get(path: str, params: dict, key: str) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"x-api-key": key})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"   (network error: {e}; retrying)", flush=True)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Rate limit not resolved: {url}")


def find_show_id(key: str) -> str:
    data = api_get("/shows", {"pagination[per]": 50}, key)["data"]
    for s in data:
        if s["attributes"]["title"].strip().lower() == SHOW_NAME.lower():
            return s["id"]
    sys.exit(f"Show '{SHOW_NAME}' not found")


def matching_episodes(key: str, show_id: str, match: str, exclude: str) -> list[dict]:
    """Search episodes server-side, then apply whole-word regex filters locally."""
    words = match.split()
    inc = re.compile(r"\b" + r"\s+".join(map(re.escape, words)) + r"\b", re.I)
    exc = re.compile(re.escape(exclude), re.I) if exclude else None
    out, page = [], 1
    while True:
        resp = api_get("/episodes", {
            "show_id": show_id, "query": match, "status": "published",
            "pagination[page]": page, "pagination[per]": 50,
        }, key)
        for ep in resp["data"]:
            t = ep["attributes"]["title"]
            if inc.search(t) and not (exc and exc.search(t)):
                out.append(ep)
        if page >= resp["meta"]["totalPages"]:
            break
        page += 1
        time.sleep(RATE_SLEEP)
    # Same segment posted twice (e.g. "-" vs "–" in title) → keep the oldest upload
    seen, uniq = set(), []
    for ep in sorted(out, key=lambda e: e["attributes"].get("created_at") or ""):
        k = re.sub(r"\s*[–—-]\s*", " - ", ep["attributes"]["title"]).strip().lower()
        if k not in seen:
            seen.add(k)
            uniq.append(ep)
    return sorted(uniq, key=lambda e: e["attributes"].get("published_at") or "")


def safe_name(s: str) -> str:
    s = s.replace("/", "-").replace(":", " -")
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)
    return re.sub(r"\s+", " ", s).strip()[:180]


def base_name(ep: dict) -> str:
    a = ep["attributes"]
    date = (a.get("published_at") or a.get("created_at") or "")[:10] or "undated"
    return f"{date}_{safe_name(a['title'])}"


def sidecar_text(ep: dict) -> str:
    a = ep["attributes"]
    desc = re.sub(r"<[^>]+>", "", a.get("description") or a.get("summary") or "").strip()
    return "\n".join([
        a["title"],
        f"Published: {a.get('published_at') or ''}",
        f"Uploaded:  {a.get('created_at') or ''}",
        f"Tags: {a.get('keywords') or ''}",
        f"Transistor episode ID: {ep['id']}",
        f"Share URL: {a.get('share_url') or ''}",
        f"Audio URL: {a.get('audio_url') or a.get('media_url') or ''}",
        "",
        desc,
        "",
    ])


def existing_files(dest: str) -> set[str]:
    r = subprocess.run(["rclone", "lsf", "--files-only", dest], capture_output=True, text=True)
    return set(r.stdout.splitlines()) if r.returncode == 0 else set()


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "ticket-archive-backup/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            return
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match", default="rant", help="whole-word title match (default: rant)")
    ap.add_argument("--exclude", default="revival", help="skip titles containing this (default: revival; '' = none)")
    ap.add_argument("--dest", default="gdrive:The Rant", help="rclone destination (default: gdrive:The Rant)")
    ap.add_argument("--dry-run", "-n", action="store_true", help="list what would be copied; copy nothing")
    args = ap.parse_args()

    key = api_key()
    show_id = find_show_id(key)
    print(f"Searching '{SHOW_NAME}' for titles matching '{args.match}'"
          + (f", excluding '{args.exclude}'" if args.exclude else "") + " …", flush=True)
    eps = matching_episodes(key, show_id, args.match, args.exclude)
    print(f"{len(eps)} episode(s) found.", flush=True)

    have = existing_files(args.dest)
    todo = [e for e in eps if f"{base_name(e)}.mp3" not in have]
    print(f"{len(eps) - len(todo)} already in {args.dest}; {len(todo)} to copy.\n", flush=True)

    if args.dry_run:
        for e in todo:
            print(f"  {base_name(e)}.mp3")
        return

    ok = failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, ep in enumerate(todo, 1):
            name = base_name(ep)
            url = ep["attributes"].get("audio_url") or ep["attributes"].get("media_url")
            print(f"[{i}/{len(todo)}] {name}", flush=True)
            if not url:
                print("   ! no audio URL — skipped", flush=True)
                failed += 1
                continue
            mp3, txt = Path(tmp, f"{name}.mp3"), Path(tmp, f"{name}.txt")
            try:
                download(url, mp3)
                txt.write_text(sidecar_text(ep))
                for f in (mp3, txt):
                    subprocess.run(["rclone", "copyto", str(f), f"{args.dest}/{f.name}"], check=True)
                print(f"   ✓ {mp3.stat().st_size / 1e6:.1f} MB uploaded", flush=True)
                ok += 1
            except Exception as ex:  # keep going; re-run picks up failures
                print(f"   ! failed: {ex}", flush=True)
                failed += 1
            finally:
                mp3.unlink(missing_ok=True)
                txt.unlink(missing_ok=True)

    print(f"\nDone: {ok} copied, {failed} failed, {len(eps) - len(todo)} already present → {args.dest}")
    if failed:
        print("Re-run the same command to retry the failures.")


if __name__ == "__main__":
    main()
