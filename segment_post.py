#!/usr/bin/env python3
"""
segment_post.py — Post an audio segment to Transistor.fm and WordPress.
=======================================================================
Interactively selects an MP3 from ~/Dropbox/The Ticket, prompts for
metadata, and publishes to:
  • Transistor.fm  — show "The Ticket Archive"
  • WordPress      — unticket.com

Usage:
    python3 segment_post.py                     # interactive file picker
    python3 segment_post.py --file audio.mp3    # skip file picker
    python3 segment_post.py --dry-run           # preview only
    python3 segment_post.py --transistor-only   # skip WordPress
    python3 segment_post.py --wp-only           # skip Transistor
    python3 segment_post.py --draft             # post WordPress as draft

Credentials (~/.segment_post_env or shell environment):
    TRANSISTOR_API_KEY
    WP_URL             (default: https://unticket.com)
    WP_USER
    WP_APP_PASSWORD
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import readline  # noqa: F401 — enables arrow-key editing in input() on macOS
except ImportError:
    pass

# ── Configuration ──────────────────────────────────────────────────────────────

TRANSISTOR_API_BASE = "https://api.transistor.fm/v1"
TRANSISTOR_SHOW     = "The Ticket Archive"
WP_URL_DEFAULT      = "https://unticket.com"
DROPBOX_ROOT        = Path("~/Dropbox/The Ticket").expanduser()
ENV_FILE            = Path("~/.segment_post_env").expanduser()
RATE_SLEEP          = 1.2        # seconds between Transistor API calls (limit: 10/10s)
RETRY_WAIT          = 12         # seconds to back off after a Transistor 429
MAX_FILES_SHOWN     = 30         # files shown in the interactive picker
WP_TIMEOUT          = 60         # seconds for regular WordPress API calls
WP_UPLOAD_TIMEOUT   = 600        # seconds for media uploads (slow server)
WP_RETRY_ATTEMPTS   = 3          # retry attempts for WordPress network errors
WP_RETRY_WAITS      = (10, 30, 60)  # back-off schedule between WP retries
LOG_FILE            = Path(__file__).parent / "logs" / "segment_post.log"

# Show name keyword → default tag list
SHOW_DEFAULT_TAGS: dict[str, list[str]] = {
    "musers":            ["The Musers", "Musers", "Gordon Keith", "George Dunham", "Craig Miller", "KTCK"],
    "hardline":          ["The Hardline", "Hardline", "Mike Rhyner", "Corby Davidson", "KTCK"],
    "bad radio":         ["BaD Radio", "Bob Sturm", "Dan McDowell", "KTCK"],
    "bad":               ["BaD Radio", "Bob Sturm", "Dan McDowell", "KTCK"],
    "afternoon":         ["The Afternoon", "Norm Hitzges", "KTCK"],
    "work in progress":  ["Work in Progress", "WIP", "KTCK"],
    "junior":            ["Junior", "KTCK"],
    "fan":               ["The Ticket", "KTCK"],
}
DEFAULT_TAGS = ["The Ticket", "KTCK"]

# ── Logging ───────────────────────────────────────────────────────────────────

log = logging.getLogger("segment_post")


def setup_logging() -> None:
    """Initialise file logging. Console output stays as plain print()."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(LOG_FILE)],
    )


# ── Env loading ────────────────────────────────────────────────────────────────


def load_env() -> None:
    """Load KEY=VALUE pairs from ENV_FILE into os.environ (without overwriting)."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# ── Transistor API ─────────────────────────────────────────────────────────────


def _t_request(method: str, url: str, api_key: str,
               data: Optional[bytes] = None) -> dict:
    """Authenticated Transistor API request with 429 retry."""
    headers = {
        "x-api-key":    api_key,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = RETRY_WAIT * (attempt + 1)
                log.warning("Transistor rate limited — waiting %ds (attempt %d)", wait, attempt + 1)
                print(f"  Rate limited — waiting {wait}s …", flush=True)
                time.sleep(wait)
                continue
            body = exc.read().decode(errors="replace")
            log.error("Transistor HTTP %d: %s", exc.code, body[:200])
            raise RuntimeError(f"Transistor HTTP {exc.code}: {body}") from exc
        except OSError as exc:
            log.warning("Transistor network error (attempt %d): %s", attempt + 1, exc)
            if attempt < 3:
                time.sleep(RETRY_WAIT)
                continue
            raise RuntimeError(f"Transistor network error: {exc}") from exc
    raise RuntimeError("Transistor: max retries exceeded")


def t_get(path: str, api_key: str, params: Optional[dict] = None) -> dict:
    url = TRANSISTOR_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    time.sleep(RATE_SLEEP)
    return _t_request("GET", url, api_key)


def t_post(path: str, api_key: str, fields: dict) -> dict:
    time.sleep(RATE_SLEEP)
    return _t_request("POST", TRANSISTOR_API_BASE + path, api_key,
                      data=urllib.parse.urlencode(fields).encode())


def t_patch(path: str, api_key: str, fields: dict) -> dict:
    time.sleep(RATE_SLEEP)
    return _t_request("PATCH", TRANSISTOR_API_BASE + path, api_key,
                      data=urllib.parse.urlencode(fields).encode())


def find_transistor_show(api_key: str) -> str:
    """Return show ID for TRANSISTOR_SHOW, paginating if needed."""
    page = 1
    while True:
        resp = t_get("/shows", api_key,
                     {"pagination[page]": page, "pagination[per]": 20})
        for show in resp.get("data", []):
            if show.get("attributes", {}).get("title", "").strip() == TRANSISTOR_SHOW:
                return show["id"]
        if page >= resp.get("meta", {}).get("totalPages", 1):
            break
        page += 1
    raise RuntimeError(f"Transistor show '{TRANSISTOR_SHOW}' not found")


def upload_to_s3(upload_url: str, file_path: Path) -> None:
    size_mb = file_path.stat().st_size / 1_048_576
    print(f"  Uploading {file_path.name} ({size_mb:.1f} MB) …", flush=True)
    with file_path.open("rb") as fh:
        data = fh.read()
    req = urllib.request.Request(upload_url, data=data, method="PUT")
    req.add_header("Content-Type", "audio/mpeg")
    with urllib.request.urlopen(req, timeout=300) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"S3 upload failed: status {resp.status}")


def post_to_transistor(mp3_path: Path, show_id: str, api_key: str,
                       title: str, description: str, tags: list[str],
                       pub_dt: datetime, dry_run: bool) -> Optional[str]:
    """Upload, create, and publish a Transistor episode. Returns episode ID."""
    if dry_run:
        print(f"  [DRY-RUN] Would post to Transistor: '{title}'")
        return "dry-run-id"

    log.info("Transistor: starting upload for '%s'", title)

    # 1. Authorize upload
    print("  Authorizing upload …", flush=True)
    auth = t_get("/episodes/authorize_upload", api_key, {"filename": mp3_path.name})
    upload_url = auth["data"]["attributes"]["upload_url"]
    audio_url  = auth["data"]["attributes"]["audio_url"]

    # 2. Upload to S3
    upload_to_s3(upload_url, mp3_path)
    log.info("Transistor: S3 upload complete")

    # 3. Create draft episode
    fields: dict = {
        "episode[show_id]":   show_id,
        "episode[title]":     title,
        "episode[audio_url]": audio_url,
    }
    if description.strip():
        fields["episode[summary]"] = description.strip()
    if tags:
        fields["episode[keywords]"] = ", ".join(tags)

    ep_resp = t_post("/episodes", api_key, fields)
    ep_id = ep_resp["data"]["id"]
    print(f"  Episode created (draft) — ID {ep_id}", flush=True)
    log.info("Transistor: episode created — ID %s", ep_id)

    # 4. Publish with publish date
    pub_str = pub_dt.strftime("%Y-%m-%d %H:%M:%S")
    t_patch(f"/episodes/{ep_id}/publish", api_key, {
        "episode[status]":       "published",
        "episode[published_at]": pub_str,
    })
    print(f"  ✓ Published to Transistor: {title}", flush=True)
    log.info("Transistor: published '%s' (ID %s) at %s", title, ep_id, pub_str)
    return ep_id


# ── WordPress API ──────────────────────────────────────────────────────────────


def _wp_request(method: str, endpoint: str, wp_url: str,
                wp_user: str, wp_pass: str,
                data: Optional[dict] = None,
                params: Optional[dict] = None) -> Any:
    """Authenticated WordPress REST API request with retry on network errors."""
    url = wp_url.rstrip("/") + "/wp-json/wp/v2" + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    creds = base64.b64encode(f"{wp_user}:{wp_pass}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type":  "application/json",
    }
    body = json.dumps(data).encode() if data is not None else None
    for attempt in range(WP_RETRY_ATTEMPTS):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=WP_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            log.error("WordPress HTTP %d on %s %s: %s", exc.code, method, endpoint, body_text[:200])
            raise RuntimeError(f"WordPress HTTP {exc.code}: {body_text}") from exc
        except OSError as exc:
            if attempt < WP_RETRY_ATTEMPTS - 1:
                wait = WP_RETRY_WAITS[attempt]
                log.warning("WordPress network error (attempt %d/%d): %s — retrying in %ds",
                            attempt + 1, WP_RETRY_ATTEMPTS, exc, wait)
                print(f"  Network error — retrying in {wait}s … ({exc})", flush=True)
                time.sleep(wait)
            else:
                log.error("WordPress request failed after %d attempts: %s", WP_RETRY_ATTEMPTS, exc)
                raise RuntimeError(f"WordPress request failed after {WP_RETRY_ATTEMPTS} attempts: {exc}") from exc
    raise RuntimeError("WordPress: retry loop exhausted")


def wp_upload_media(mp3_path: Path, wp_url: str, wp_user: str, wp_pass: str) -> str:
    """Upload an MP3 to the WordPress media library. Returns source_url."""
    size_mb = mp3_path.stat().st_size / 1_048_576
    print(f"  Uploading {mp3_path.name} ({size_mb:.1f} MB) to WordPress …", flush=True)
    log.info("WordPress: uploading media %s (%.1f MB)", mp3_path.name, size_mb)
    creds = base64.b64encode(f"{wp_user}:{wp_pass}".encode()).decode()
    url = wp_url.rstrip("/") + "/wp-json/wp/v2/media"
    with mp3_path.open("rb") as fh:
        data = fh.read()
    for attempt in range(2):  # one retry for large uploads
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Basic {creds}")
        req.add_header("Content-Type", "audio/mpeg")
        req.add_header("Content-Disposition", f'attachment; filename="{mp3_path.name}"')
        try:
            with urllib.request.urlopen(req, timeout=WP_UPLOAD_TIMEOUT) as resp:
                result = json.loads(resp.read().decode())
                source_url = result.get("source_url", "")
                print(f"  ✓ Media uploaded: {source_url}", flush=True)
                log.info("WordPress: media uploaded — %s", source_url)
                return source_url
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            log.error("WordPress media upload HTTP %d: %s", exc.code, body_text[:200])
            raise RuntimeError(f"WordPress media upload HTTP {exc.code}: {body_text}") from exc
        except OSError as exc:
            if attempt == 0:
                log.warning("WordPress upload failed (attempt 1): %s — retrying in 30s", exc)
                print(f"  Upload interrupted — retrying in 30s … ({exc})", flush=True)
                time.sleep(30)
            else:
                log.error("WordPress upload failed after 2 attempts: %s", exc)
                raise RuntimeError(f"WordPress upload failed after 2 attempts: {exc}") from exc
    raise RuntimeError("WordPress: upload retry exhausted")


def wp_get_or_create_tag(name: str, wp_url: str, wp_user: str, wp_pass: str) -> int:
    """Return WordPress tag ID for name, creating it if needed."""
    results = _wp_request("GET", "/tags", wp_url, wp_user, wp_pass,
                          params={"search": name, "per_page": 10})
    if isinstance(results, list):
        for tag in results:
            if tag.get("name", "").lower() == name.lower():
                return int(tag["id"])
    # Create new tag
    new_tag = _wp_request("POST", "/tags", wp_url, wp_user, wp_pass, {"name": name})
    return int(new_tag["id"])


def post_to_wordpress(title: str, description: str, tags: list[str],
                      pub_dt: datetime, transistor_ep_id: Optional[str],
                      mp3_path: Optional[Path],
                      wp_url: str, wp_user: str, wp_pass: str,
                      dry_run: bool, draft: bool = False) -> tuple[Optional[int], Optional[str]]:
    """Upload MP3 to WP media, create post with [audio] shortcode. Returns (post_id, post_url)."""
    if dry_run:
        print(f"  [DRY-RUN] Would upload MP3 and post to WordPress: '{title}'")
        return None, None

    # Upload MP3 to WordPress media library
    wp_audio_url: Optional[str] = None
    if mp3_path is not None:
        wp_audio_url = wp_upload_media(mp3_path, wp_url, wp_user, wp_pass)

    # Build post content
    content_parts: list[str] = []
    if wp_audio_url:
        content_parts.append(f'[audio mp3="{wp_audio_url}"][/audio]')
    elif transistor_ep_id and transistor_ep_id != "dry-run-id":
        # Fallback: Transistor embed if no WP upload
        embed_src = f"https://share.transistor.fm/e/{transistor_ep_id}"
        content_parts.append(
            f'<iframe width="100%" height="180" frameborder="no" scrolling="no" '
            f'seamless src="{embed_src}"></iframe>'
        )
    if description.strip():
        for para in description.strip().split("\n\n"):
            para = para.strip()
            if para:
                content_parts.append(f"<p>{para}</p>")

    content = "\n\n".join(content_parts)

    # Resolve tag IDs (look up or create)
    tag_ids: list[int] = []
    if tags:
        print("  Resolving WordPress tags …", flush=True)
        for tag_name in tags:
            tag_name = tag_name.strip()
            if tag_name:
                tag_id = wp_get_or_create_tag(tag_name, wp_url, wp_user, wp_pass)
                tag_ids.append(tag_id)

    post_data: dict = {
        "title":   title,
        "content": content,
        "status":  "draft" if draft else "publish",
        "date":    pub_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "tags":    tag_ids,
    }

    log.info("WordPress: creating post '%s'", title)
    result = _wp_request("POST", "/posts", wp_url, wp_user, wp_pass, post_data)
    post_id   = result.get("id")
    post_link = result.get("link", "")
    status    = result.get("status", "")
    print(f"  ✓ WordPress post created — ID {post_id} ({status})", flush=True)
    if post_link:
        print(f"    {post_link}", flush=True)
    log.info("WordPress: post published — ID %s  %s", post_id, post_link)
    return post_id, post_link


# ── File selection ─────────────────────────────────────────────────────────────


def scan_mp3_files(root: Path) -> list[Path]:
    """Return MP3 files under root, sorted newest-first by mtime."""
    if not root.exists():
        return []
    return sorted(root.rglob("*.mp3"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def select_file_interactive(files: list[Path]) -> Path:
    """Show a numbered list and return the selected path."""
    if not files:
        sys.exit(
            f"No MP3 files found under {DROPBOX_ROOT}\n"
            "Use --file <path> to specify a file directly."
        )

    display = files[:MAX_FILES_SHOWN]
    print(f"\nRecent MP3s in {DROPBOX_ROOT}  (newest first)\n")
    for i, p in enumerate(display, 1):
        try:
            rel = p.relative_to(DROPBOX_ROOT)
        except ValueError:
            rel = p
        mtime   = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        size_mb = p.stat().st_size / 1_048_576
        print(f"  {i:>3}.  [{mtime}]  {rel}  ({size_mb:.0f} MB)")

    if len(files) > MAX_FILES_SHOWN:
        print(f"\n  … {len(files) - MAX_FILES_SHOWN} more files not shown (use --file to specify)")

    print()
    while True:
        raw = input("  Pick a number (or enter a full file path): ").strip()
        if not raw:
            continue
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(display):
                return display[idx]
            print(f"  Enter a number between 1 and {len(display)}.")
        else:
            p = Path(raw).expanduser()
            if p.exists():
                return p
            print(f"  File not found: {raw}")


# ── Metadata helpers ───────────────────────────────────────────────────────────

_FNAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_\d{4}_(.+)$")


def parse_filename(path: Path) -> tuple[Optional[str], Optional[datetime]]:
    """Parse YYYY-MM-DD_HHMM_Show_Name → (show_title, date). Returns (None, None) on mismatch."""
    m = _FNAME_RE.match(path.stem)
    if not m:
        return None, None
    date_obj   = datetime.strptime(m.group(1), "%Y-%m-%d")
    show_title = m.group(2).replace("_", " ")
    return show_title, date_obj


def default_tags_for(show_name: str) -> list[str]:
    show_lower = show_name.lower()
    for keyword, tags in SHOW_DEFAULT_TAGS.items():
        if keyword in show_lower:
            return list(tags)
    return list(DEFAULT_TAGS)


# ── Prompt helpers ─────────────────────────────────────────────────────────────


def prompt(label: str, default: str = "") -> str:
    """Single-line prompt. Empty input accepts default."""
    suffix = f" [{default}]" if default else ""
    raw = input(f"  {label}{suffix}: ").strip()
    return raw if raw else default


def prompt_multiline(label: str) -> str:
    """Multi-line prompt terminated by a blank line."""
    print(f"  {label} (blank line to finish):")
    lines: list[str] = []
    while True:
        try:
            line = input("    ")
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return "\n\n".join(
        " ".join(chunk.split())            # collapse internal whitespace per line
        for chunk in "\n".join(lines).split("\n\n")
        if chunk.strip()
    )


def prompt_datetime(default: Optional[datetime] = None) -> datetime:
    """Prompt for date and optional time. Returns datetime."""
    default_date = default.strftime("%m/%d/%Y") if default else ""
    default_time = "12:00"
    while True:
        date_str = prompt("Date (MM/DD/YYYY)", default_date)
        time_str = prompt("Time HH:MM 24-hr CT", default_time)
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H:%M")
        except ValueError:
            print("  Invalid format — try again.")


# ── Partial-failure helper ────────────────────────────────────────────────────


def _report_partial(transistor_ep_id: Optional[str], wp_post_url: Optional[str],
                    mp3_path: Path, transistor_only: bool, wp_only: bool) -> None:
    """Print a targeted retry hint if only one side succeeded."""
    t_ok = transistor_ep_id and transistor_ep_id != "dry-run-id"
    wp_ok = bool(wp_post_url)
    if t_ok and not wp_ok and not transistor_only:
        print(f"\n  Transistor succeeded (episode ID {transistor_ep_id}).")
        print(f"  Retry WordPress with:")
        print(f"    python3 segment_post.py --wp-only --file '{mp3_path}'")
        log.warning("Partial success: Transistor OK (ID %s), WordPress failed", transistor_ep_id)
    elif wp_ok and not t_ok and not wp_only:
        print(f"\n  WordPress succeeded ({wp_post_url}).")
        print(f"  Retry Transistor with:")
        print(f"    python3 segment_post.py --transistor-only --file '{mp3_path}'")
        log.warning("Partial success: WordPress OK, Transistor failed")


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post an audio segment to Transistor.fm + WordPress."
    )
    parser.add_argument("--file", type=Path, metavar="MP3",
                        help="MP3 to post (skips interactive file picker)")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Preview without posting anything")
    parser.add_argument("--transistor-only", action="store_true",
                        help="Skip WordPress")
    parser.add_argument("--wp-only", action="store_true",
                        help="Skip Transistor")
    parser.add_argument("--draft", action="store_true",
                        help="Create WordPress post as draft instead of publishing")
    args = parser.parse_args()

    setup_logging()
    log.info("─── segment_post started ───")

    # ── Load credentials ──
    load_env()
    transistor_key = os.environ.get("TRANSISTOR_API_KEY", "")
    wp_url   = os.environ.get("WP_URL",          WP_URL_DEFAULT).rstrip("/")
    wp_user  = os.environ.get("WP_USER",         "")
    wp_pass  = os.environ.get("WP_APP_PASSWORD", "")

    # Validate
    missing: list[str] = []
    if not args.wp_only and not transistor_key:
        missing.append("TRANSISTOR_API_KEY")
    if not args.transistor_only and (not wp_user or not wp_pass):
        if args.dry_run:
            pass  # allow dry-run without WP creds
        else:
            missing.append("WP_USER / WP_APP_PASSWORD")

    if missing:
        print("Missing credentials:", ", ".join(missing))
        print(f"Add them to {ENV_FILE}  (KEY=value, one per line)")
        if not args.dry_run:
            sys.exit(1)

    # ── File selection ──
    print("\n╔══════════════════════════════════════════╗")
    print("║         Segment Post — The Ticket        ║")
    print("╚══════════════════════════════════════════╝")

    if args.file:
        mp3_path = args.file.expanduser().resolve()
        if not mp3_path.exists():
            sys.exit(f"File not found: {mp3_path}")
        print(f"\n  File: {mp3_path}")
    else:
        print(f"\nScanning {DROPBOX_ROOT} for MP3 files …")
        files    = scan_mp3_files(DROPBOX_ROOT)
        mp3_path = select_file_interactive(files)

    # ── Parse filename for defaults ──
    show_title, date_obj = parse_filename(mp3_path)

    # ── Metadata prompts ──
    print("\n─── Episode Metadata ───────────────────────────────────────────────────────\n")

    # Title
    if show_title and date_obj:
        default_title = f"{show_title} - {date_obj.strftime('%m.%d.%Y')}"
    else:
        default_title = mp3_path.stem.replace("_", " ")
    title = prompt("Title", default_title)

    # Description / show notes
    print()
    description = prompt_multiline("Description / show notes")

    # Tags
    suggested = default_tags_for(show_title or title)
    print()
    tags_raw = prompt("Tags (comma-separated)", ", ".join(suggested))
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    # Publish date / time
    print()
    print("  Publish date / time:")
    pub_dt = prompt_datetime(date_obj)

    # ── Confirmation ──
    print("\n─── Summary ────────────────────────────────────────────────────────────────\n")
    print(f"  File:        {mp3_path.name}")
    desc_preview = (description[:72] + " …") if len(description) > 75 else description
    print(f"  Title:       {title}")
    print(f"  Description: {desc_preview or '(none)'}")
    print(f"  Tags:        {', '.join(tags) or '(none)'}")
    print(f"  Publish at:  {pub_dt.strftime('%Y-%m-%d %H:%M')} CT")
    if not args.wp_only:
        print(f"  → Transistor: {TRANSISTOR_SHOW}")
    if not args.transistor_only:
        status_label = "draft" if args.draft else "published"
        print(f"  → WordPress:  {wp_url}  ({status_label})")
    if args.dry_run:
        print("\n  *** DRY-RUN — nothing will actually be posted ***")

    print()
    confirm = input("  Proceed? [Y/n] ").strip().lower()
    if confirm not in ("", "y", "yes"):
        print("  Aborted.")
        sys.exit(0)

    # ── Post to Transistor and WordPress ──
    transistor_ep_id: Optional[str] = None
    wp_post_url: Optional[str] = None

    try:
        if not args.wp_only:
            print("\n─── Transistor ─────────────────────────────────────────────────────────────\n")
            if args.dry_run:
                print(f"  [DRY-RUN] Would post to Transistor show '{TRANSISTOR_SHOW}'")
                transistor_ep_id = "dry-run-id"
            else:
                print(f"  Looking up show '{TRANSISTOR_SHOW}' …", flush=True)
                show_id = find_transistor_show(transistor_key)
                print(f"  Show ID: {show_id}", flush=True)
                transistor_ep_id = post_to_transistor(
                    mp3_path, show_id, transistor_key,
                    title, description, tags, pub_dt, dry_run=False,
                )

        if not args.transistor_only:
            if not wp_user or not wp_pass:
                print("\n  WordPress credentials not set — skipping.")
            else:
                print("\n─── WordPress ──────────────────────────────────────────────────────────────\n")
                _, wp_post_url = post_to_wordpress(
                    title, description, tags, pub_dt,
                    transistor_ep_id,
                    mp3_path,
                    wp_url, wp_user, wp_pass,
                    args.dry_run, args.draft,
                )

    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
        log.warning("Interrupted by user (Ctrl+C)")
        _report_partial(transistor_ep_id, wp_post_url, mp3_path,
                        args.transistor_only, args.wp_only)
        sys.exit(130)

    except Exception as exc:
        log.error("Fatal error: %s", exc, exc_info=True)
        print(f"\n  Error: {exc}")
        _report_partial(transistor_ep_id, wp_post_url, mp3_path,
                        args.transistor_only, args.wp_only)
        sys.exit(1)

    log.info("─── segment_post finished (transistor=%s, wp=%s) ───",
             transistor_ep_id or "skipped", wp_post_url or "skipped")
    print("\n─── Done ───────────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
