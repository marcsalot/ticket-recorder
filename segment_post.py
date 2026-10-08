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
import xmlrpc.client
from datetime import datetime, timedelta, timezone
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
CUSTOM_TAGS_FILE    = Path("~/.segment_post_tags.json").expanduser()
POST_STATE_FILE     = Path("~/.segment_post_state.json").expanduser()

# Show name keyword → default tag list
# Keys are substrings matched (case-insensitive) against the Dropbox folder name.
SHOW_DEFAULT_TAGS: dict[str, list[str]] = {
    "musers":            ["The Musers", "Musers", "Gordon Keith", "George Dunham", "Craig Miller", "KTCK"],
    "hardline":          ["The Hardline", "Hardline", "Corby Davidson", "Bob Sturm", "Dave Lane", "KTCK"],
    "bad radio":         ["BaD Radio", "BaD", "Bob Sturm", "Dan McDowell", "KTCK"],
    "bad":               ["BaD Radio", "BaD", "Bob Sturm", "Dan McDowell", "KTCK"],
    "sweet spot":        ["The Sweet Spot", "Sweet Spot", "Sean Bass", "Mino", "Monty", "KTCK"],
    "invasion":          ["The Invasion", "Invasion", "Donovan Lewis", "Matt McClearin", "KTCK"],
    "work in progress":  ["Work in Progress", "WIP", "DJ", "Monty", "KTCK"],
    "rant":               ["The Rant", "Rant", "Gordon Keith", "Dave Lane", "Corby Davidson", "KTCK"],
}
DEFAULT_TAGS = ["The Ticket", "KTCK"]

# Filename substring → replacement title (case-insensitive)
FILENAME_ALIASES: dict[str, str] = {
    "odeck": "Observation Deck",
    "cunt":  "C.U.N.T.",          # CUNT → C.U.N.T. in post title
}

# Filename/title substrings → (tags_to_add, tags_to_remove)
# Each entry: ([keywords_to_match], [tags_to_inject], [tags_to_drop])
AUTO_TAG_RULES: list[tuple[list[str], list[str], list[str]]] = [
    (["trifecta"],                                        ["Donovan Lewis"],  []),
    (["the rant revival", "c.u.n.t.", "cunt"],           ["Gordon Keith"],   []),
    (["fake", "touchdown bros", "marge schwimdiddler"],   ["840 bit"],        []),
    (["wtds"],                                            ["wtds"],           []),
]

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
        fields["episode[description]"] = description.strip()  # show notes (full HTML)
    if tags:
        fields["episode[keywords]"] = ", ".join(tags)

    ep_resp = t_post("/episodes", api_key, fields)
    ep_id = ep_resp["data"]["id"]
    print(f"  Episode created (draft) — ID {ep_id}", flush=True)
    log.info("Transistor: episode created — ID %s", ep_id)

    # 4. Publish with publish date
    pub_str = pub_dt.strftime("%Y-%m-%d %H:%M:%S")
    fields = {
        "episode[status]": "published",
        "episode[published_at]": pub_str,
    }
    if pub_dt > datetime.now():
        fields["episode[status]"] = "scheduled"
    
    t_patch(f"/episodes/{ep_id}/publish", api_key, fields)
    label = "Scheduled" if pub_dt > datetime.now() else "Published"
    print(f"  ✓ {label} on Transistor: {title}", flush=True)
    log.info("Transistor: %s '%s' (ID %s) at %s", label.lower(), title, ep_id, pub_str)
    return ep_id, audio_url  # audio_url used by WordPress sideload


# ── WordPress API (REST via X-WP-Auth) ───────────────────────────────────────────────
# Uses the REST API with credentials in X-WP-Auth (a custom header Apache
# doesn't strip) authenticated by the Ticket Post Auth plugin.


def _wp_headers(wp_user: str, wp_pass: str) -> dict:
    creds = base64.b64encode(f"{wp_user}:{wp_pass}".encode()).decode()
    return {
        "X-WP-Auth":   f"Basic {creds}",
        "User-Agent":  "Mozilla/5.0",
    }


def _wp_rest(method: str, endpoint: str, wp_url: str, wp_user: str, wp_pass: str,
             data: Optional[dict] = None, params: Optional[dict] = None,
             raw_body: Optional[bytes] = None,
             extra_headers: Optional[dict] = None) -> Any:
    """Authenticated WordPress REST API request via X-WP-Auth header."""
    url = wp_url.rstrip("/") + "/wp-json/wp/v2" + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = _wp_headers(wp_user, wp_pass)
    if extra_headers:
        headers.update(extra_headers)
    body: Optional[bytes] = None
    if raw_body is not None:
        body = raw_body
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    for attempt in range(WP_RETRY_ATTEMPTS):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=WP_UPLOAD_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            log.error("WordPress REST %d on %s %s: %s", exc.code, method, endpoint, body_text[:200])
            raise RuntimeError(f"WordPress REST {exc.code}: {body_text}") from exc
        except OSError as exc:
            if attempt < WP_RETRY_ATTEMPTS - 1:
                wait = WP_RETRY_WAITS[attempt]
                log.warning("WordPress network error (attempt %d): %s — retrying in %ds",
                            attempt + 1, exc, wait)
                print(f"  Network error — retrying in {wait}s …", flush=True)
                time.sleep(wait)
            else:
                raise RuntimeError(f"WordPress REST failed after {WP_RETRY_ATTEMPTS} attempts: {exc}") from exc
    raise RuntimeError("WordPress: REST retry exhausted")


def wp_get_or_create_tag(name: str, wp_url: str, wp_user: str, wp_pass: str) -> int:
    results = _wp_rest("GET", "/tags", wp_url, wp_user, wp_pass,
                       params={"search": name, "per_page": 10})
    if isinstance(results, list):
        for tag in results:
            if tag.get("name", "").lower() == name.lower():
                return int(tag["id"])
    try:
        new_tag = _wp_rest("POST", "/tags", wp_url, wp_user, wp_pass, {"name": name})
        return int(new_tag["id"])
    except RuntimeError as exc:
        err = str(exc)
        if "term_exists" in err:
            m = re.search(r'"term_id"\s*:\s*(\d+)', err)
            if m:
                return int(m.group(1))
        raise


def wp_get_or_create_category(name: str, wp_url: str, wp_user: str, wp_pass: str) -> int:
    results = _wp_rest("GET", "/categories", wp_url, wp_user, wp_pass,
                       params={"search": name, "per_page": 10})
    if isinstance(results, list):
        for cat in results:
            if cat.get("name", "").lower() == name.lower():
                return int(cat["id"])
    new_cat = _wp_rest("POST", "/categories", wp_url, wp_user, wp_pass, {"name": name})
    return int(new_cat["id"])


def wp_sideload_media(audio_url: str, filename: str,
                      wp_url: str, wp_user: str, wp_pass: str) -> str:
    """Tell WordPress to download the audio from a URL and add it to the media library.

    Sends a tiny JSON payload (just a URL) — no binary data, no WAF issues.
    WordPress fetches the file internally from Transistor's CDN.
    Requires the Ticket Post Auth plugin v3.0+ to be active.
    """
    print(f"  Sideloading {filename} from Transistor CDN …", flush=True)
    log.info("WordPress: sideloading %s from %s", filename, audio_url)
    url = wp_url.rstrip("/") + "/wp-json/ticket/v1/sideload"
    headers = _wp_headers(wp_user, wp_pass)
    headers["Content-Type"] = "application/json"
    payload = json.dumps({"url": audio_url, "filename": filename}).encode()
    for attempt in range(2):
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=WP_UPLOAD_TIMEOUT) as resp:
                result = json.loads(resp.read().decode())
                src_url = result.get("source_url", "")
                print(f"  ✓ Media sideloaded: {src_url}", flush=True)
                log.info("WordPress: media sideloaded — %s", src_url)
                return src_url
        except urllib.error.HTTPError as exc:
            body_err = exc.read().decode(errors="replace")
            log.error("WordPress sideload HTTP %d: %s", exc.code, body_err[:200])
            if attempt == 0:
                print(f"  Sideload error ({exc.code}) — retrying in 15s …", flush=True)
                time.sleep(15)
            else:
                raise RuntimeError(f"WordPress sideload failed: {exc.code}: {body_err}") from exc
        except OSError as exc:
            if attempt == 0:
                print(f"  Network error — retrying in 15s … ({exc})", flush=True)
                time.sleep(15)
            else:
                raise RuntimeError(f"WordPress sideload network error: {exc}") from exc
    raise RuntimeError("WordPress: sideload retry exhausted")


def wp_upload_media(mp3_path: Path, wp_url: str, wp_user: str, wp_pass: str) -> str:
    """Alias kept for compatibility — delegates to wp_sideload_media if audio_url set."""
    raise NotImplementedError("Use wp_sideload_media with a Transistor audio URL")


def post_to_wordpress(title: str, description: str, tags: list[str],
                      pub_dt: datetime, transistor_ep_id: Optional[str],
                      mp3_path: Optional[Path],
                      wp_url: str, wp_user: str, wp_pass: str,
                      dry_run: bool, draft: bool = False,
                      category_name: Optional[str] = None,
                      transistor_audio_url: Optional[str] = None) -> tuple[Optional[int], Optional[str]]:
    """Upload MP3 and create WP post via REST API. Returns (post_id, post_url)."""
    if dry_run:
        print(f"  [DRY-RUN] Would upload MP3 and post to WordPress: '{title}'")
        return None, None

    # Resolve category
    category_ids: list[int] = []
    if category_name:
        print(f"  Resolving category '{category_name}' …", flush=True)
        cat_id = wp_get_or_create_category(category_name, wp_url, wp_user, wp_pass)
        category_ids = [cat_id]

    # Sideload audio from Transistor CDN (no binary upload needed)
    wp_audio_url: Optional[str] = None
    if transistor_audio_url and mp3_path is not None:
        wp_audio_url = wp_sideload_media(
            transistor_audio_url, mp3_path.name, wp_url, wp_user, wp_pass
        )

    # Build post content
    content_parts: list[str] = []
    if description.strip():
        for para in description.strip().split("\n\n"):
            para = para.strip()
            if para:
                content_parts.append(f"<p>{para}</p>")
    if wp_audio_url:
        content_parts.append(f'[audio mp3="{wp_audio_url}"][/audio]')
    elif transistor_ep_id and transistor_ep_id != "dry-run-id":
        embed_src = f"https://share.transistor.fm/e/{transistor_ep_id}"
        content_parts.append(
            f'<iframe width="100%" height="180" frameborder="no" scrolling="no" '
            f'seamless src="{embed_src}"></iframe>'
        )
    content = "\n\n".join(content_parts)

    # Resolve tag IDs
    tag_ids: list[int] = []
    if tags:
        print(f"  Resolving {len(tags)} tags …", flush=True)
        for tag_name in tags:
            tag_name = tag_name.strip()
            if tag_name:
                tag_ids.append(wp_get_or_create_tag(tag_name, wp_url, wp_user, wp_pass))

    post_data: dict = {
        "title":        title,
        "content":      content,
        "status":       "draft" if draft else "publish",
        "date_gmt":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "tags":         tag_ids,
        "categories":   category_ids,
    }

    log.info("WordPress: creating post '%s'", title)
    result = _wp_rest("POST", "/posts", wp_url, wp_user, wp_pass, post_data)
    post_id   = result.get("id")
    post_link = result.get("link", "")
    status    = result.get("status", "")
    print(f"  ✓ WordPress post created — ID {post_id} ({status})", flush=True)
    if post_link:
        print(f"    {post_link}", flush=True)
    log.info("WordPress: post created — ID %s  %s", post_id, post_link)
    return post_id, post_link


# ── File selection ─────────────────────────────────────────────────────────────


def _mtime_safe(p: Path) -> float:
    """Return mtime, or 0.0 if file disappeared during scan (Dropbox sync race)."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def scan_mp3_files(root: Path) -> list[Path]:
    """Return MP3 files under root, sorted newest-first by mtime.

    Retries up to 3 times on InterruptedError (EINTR), which Dropbox
    can trigger while syncing a large folder tree.
    """
    if not root.exists():
        return []
    for attempt in range(3):
        try:
            files = list(root.rglob("*.mp3"))
            return sorted(files, key=_mtime_safe, reverse=True)
        except InterruptedError:
            if attempt < 2:
                print(f"  Scan interrupted by Dropbox — retrying ({attempt + 1}/3) …",
                      flush=True)
                time.sleep(2)
            else:
                raise
    return []  # unreachable


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

_FNAME_RE         = re.compile(r"^(\d{4}-\d{2}-\d{2})_\d{4}_(.+)$")   # VPS recorder: YYYY-MM-DD_HHMM_Show
_ARCHIVE_DATE_RE  = re.compile(r"\s+(\d{4})(\d{2})(\d{2})\s*$")           # trailing YYYYMMDD
_DATED_PREFIX_RE  = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")             # YYYY-MM-DD_ prefix (no time)


def parse_filename(path: Path) -> tuple[Optional[str], Optional[datetime]]:
    """Parse YYYY-MM-DD_HHMM_Show_Name → (show_title, date). Returns (None, None) on mismatch."""
    m = _FNAME_RE.match(path.stem)
    if not m:
        return None, None
    date_obj   = datetime.strptime(m.group(1), "%Y-%m-%d")
    show_title = m.group(2).replace("_", " ")
    return show_title, date_obj


def parse_archive_filename(path: Path) -> tuple[str, str, Optional[datetime], bool]:
    """
    Parse a Dropbox archive filename.  Returns (segment_name, show_folder, date_obj, title_complete).

    Handles two formats:
      1. 'Segment Name 20260520.mp3'      — trailing YYYYMMDD; title = 'Segment - Folder M.D.YYYY'
      2. 'YYYY-MM-DD_Full Title.mp3'      — date prefix, no time; title = 'Full Title' as-is

    title_complete=True means segment_name is already the full title; don't append folder/date.
    """
    stem = path.stem

    # Determine show folder from first path component under DROPBOX_ROOT
    try:
        rel_parts = path.relative_to(DROPBOX_ROOT).parts
        folder = rel_parts[0] if len(rel_parts) > 1 else ""
    except ValueError:
        folder = path.parent.name

    # Format 1: trailing YYYYMMDD  (e.g. 'Fake Jason Kidd 20260519')
    m = _ARCHIVE_DATE_RE.search(stem)
    if m:
        name = stem[:m.start()].strip()
        try:
            date_obj: Optional[datetime] = datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3))
            )
        except ValueError:
            date_obj = None
        return name, folder, date_obj, False

    # Format 2: YYYY-MM-DD_ prefix without time  (e.g. '2024-03-09_Super Bowl – The Sweet Spot 2.7.2024')
    m2 = _DATED_PREFIX_RE.match(stem)
    if m2:
        try:
            date_obj = datetime.strptime(m2.group(1), "%Y-%m-%d")
        except ValueError:
            date_obj = None
        name = m2.group(2).strip()
        return name, folder, date_obj, True   # title_complete: use name as-is

    return stem, folder, None, False


# ── Post state (last Transistor publish time) ───────────────────────────────────────


def _load_post_state() -> dict:
    if POST_STATE_FILE.exists():
        try:
            return json.loads(POST_STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def get_last_transistor_dt() -> Optional[datetime]:
    """Return the datetime used for the last Transistor publish, or None."""
    dt_str = _load_post_state().get("last_transistor_dt")
    if dt_str:
        try:
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return None


def save_last_transistor_dt(dt: datetime) -> None:
    """Persist the Transistor publish datetime after a successful post."""
    state = _load_post_state()
    state["last_transistor_dt"] = dt.strftime("%Y-%m-%d %H:%M")
    POST_STATE_FILE.write_text(json.dumps(state, indent=2))


def load_custom_tags() -> dict[str, list[str]]:
    """Load per-show tag overrides from ~/.segment_post_tags.json."""
    if CUSTOM_TAGS_FILE.exists():
        try:
            return json.loads(CUSTOM_TAGS_FILE.read_text())
        except Exception:
            pass
    return {}


def save_custom_tags(show_name: str, tags: list[str]) -> None:
    """Persist tags for show_name to ~/.segment_post_tags.json."""
    custom = load_custom_tags()
    custom[show_name] = tags
    CUSTOM_TAGS_FILE.write_text(json.dumps(custom, indent=2))


def default_tags_for(show_name: str) -> list[str]:
    # 1. Persisted custom tags take priority (exact show name match)
    custom = load_custom_tags()
    if show_name and show_name in custom:
        return list(custom[show_name])
    # 2. Script defaults (substring match)
    show_lower = show_name.lower()
    for keyword, tags in SHOW_DEFAULT_TAGS.items():
        if keyword in show_lower:
            return list(tags)
    return list(DEFAULT_TAGS)


def auto_tags_for(stem: str, title: str) -> tuple[list[str], list[str]]:
    """Return (tags_to_add, tags_to_remove) based on filename stem and post title."""
    combined = (stem + " " + title).lower()
    add: list[str] = []
    remove: list[str] = []
    for rule in AUTO_TAG_RULES:
        keywords, tags_add = rule[0], rule[1]
        tags_remove = rule[2] if len(rule) > 2 else []
        if any(kw in combined for kw in keywords):
            for t in tags_add:
                if t not in add:
                    add.append(t)
            for t in tags_remove:
                if t not in remove:
                    remove.append(t)
    return add, remove


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
        " ".join(chunk.split())
        for chunk in "\n".join(lines).split("\n\n")
        if chunk.strip()
    )


def prompt_tags(show_name: str, extra_tags: Optional[list[str]] = None,
               remove_tags: Optional[list[str]] = None) -> list[str]:
    """
    Show default tags, then accept a single +add / -remove edit line.
    Examples:  +Donovan Lewis, +Matt      (add two)
               -Craig Miller              (remove one)
               +Bob, -KTCK               (mix)
    Enter with no input keeps the defaults unchanged.
    If the user edits the tags, offer to save them as the new default for
    this show so they appear automatically next time.
    """
    defaults = default_tags_for(show_name)
    if extra_tags:
        for t in extra_tags:
            if t not in defaults:
                defaults.append(t)
    if remove_tags:
        rl = [r.lower() for r in remove_tags]
        defaults = [t for t in defaults if t.lower() not in rl]
    saved_marker = " (saved)" if show_name in load_custom_tags() else ""
    print(f"\n  Tags ({show_name or 'general'}{saved_marker}):")
    print(f"    {', '.join(defaults)}")
    raw = prompt("  Edit (+add / -remove, Enter to keep)", "")
    if not raw.strip():
        return list(defaults)
    tags = list(defaults)
    for part in raw.split(","):
        part = part.strip()
        if part.startswith("+"):
            t = part[1:].strip()
            if t and t not in tags:
                tags.append(t)
        elif part.startswith("-"):
            t = part[1:].strip().lower()
            tags = [x for x in tags if x.lower() != t]
        elif part and part not in tags:   # bare word → treat as addition
            tags.append(part)
    print(f"  Tags: {', '.join(tags)}")
    # Offer to save as new default for this show
    if tags != defaults and show_name:
        save_q = input(f"  Save as default for '{show_name}'? [Y/n] ").strip().lower()
        if save_q in ("", "y", "yes"):
            save_custom_tags(show_name, tags)
            print(f"  Saved.")
    return tags


def prompt_datetime(default: Optional[datetime] = None) -> datetime:
    """Prompt for date and optional time. Returns datetime."""
    default_date = default.strftime("%m/%d/%Y") if default else ""
    default_time = default.strftime("%H:%M") if default else "12:00"
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

    # WP_ENABLED=0 in ~/.segment_post_env disables WordPress permanently
    # (equivalent to always passing --transistor-only)
    wp_enabled = os.environ.get("WP_ENABLED", "1").strip().lower()
    if wp_enabled in ("0", "false", "no", "off"):
        args.transistor_only = True

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

    # ── Parse filename — recorder format first, then archive format ──
    show_title_rec, date_from_rec = parse_filename(mp3_path)
    if show_title_rec:
        # VPS recorder format: YYYY-MM-DD_HHMM_Show_Name.mp3
        show_name     = show_title_rec
        date_obj      = date_from_rec
        default_title = (f"{show_name} - {date_obj.strftime('%m.%d.%Y')}"
                         if date_obj else show_name)
    else:
        seg_name, show_name, date_obj, title_complete = parse_archive_filename(mp3_path)
        # Apply filename title aliases (e.g. Odeck → Observation Deck)
        for alias_key, alias_val in FILENAME_ALIASES.items():
            seg_name = re.sub(alias_key, alias_val, seg_name, flags=re.IGNORECASE)
        if title_complete:
            # YYYY-MM-DD_ prefix format: title already contains segment + show context
            default_title = seg_name
        elif date_obj:
            date_str      = f"{date_obj.month}.{date_obj.day}.{date_obj.year}"
            default_title = (f"{seg_name} - {show_name} {date_str}"
                             if show_name else f"{seg_name} - {date_str}")
        else:
            default_title = (f"{seg_name} - {show_name}" if show_name else seg_name)

    # ── Metadata prompts ──
    print("\n─── Episode Metadata ───────────────────────────────────────────────────────\n")

    # Transistor publish date — ask first if no date in filename so title can include it
    last_dt = get_last_transistor_dt()
    if date_obj is None:
        print("  No date found in filename — enter air date first (used in title):")
        # Can't apply last+1min yet (we don't know the date), so default to last date/noon
        if last_dt:
            suggested_dt = last_dt + timedelta(minutes=1)
            print(f"  (Last posted {last_dt.strftime('%m/%d/%Y %H:%M')} — suggesting +1 min as default time)")
        else:
            suggested_dt = None
        transistor_dt = prompt_datetime(suggested_dt)
        date_obj = transistor_dt  # use confirmed date for title default
        date_str = f"{date_obj.month}.{date_obj.day}.{date_obj.year}"
        default_title = (f"{default_title} {date_str}"  # append date to the dateless default
                         if show_name not in default_title.split(" - ", 1)[-1]  # avoid double show name
                         else f"{default_title} {date_str}")
        date_prompted_early = True
    else:
        date_prompted_early = False

    title = prompt("Title", default_title)

    print()
    description = prompt("Description (optional, Enter to skip)", "")

    # Tags — show defaults + auto-injected tags, single +/- edit line
    _add_tags, _remove_tags = auto_tags_for(mp3_path.stem, title)
    tags = prompt_tags(show_name, extra_tags=_add_tags, remove_tags=_remove_tags)

    # WordPress category — default to show folder name
    category_name: Optional[str] = None
    if not args.transistor_only:
        print()
        default_cat = show_name
        category_name = prompt("WordPress category", default_cat)
        while not category_name:
            print("  Category is required for WordPress.")
            category_name = prompt("WordPress category")

    # Transistor publish date — skip if already prompted above
    if not date_prompted_early:
        print()
        if last_dt and last_dt.date() == date_obj.date():
            suggested_dt = last_dt + timedelta(minutes=1)
            print(f"  Transistor publish date / time  (last posted {last_dt.strftime('%H:%M')} — suggesting +1 min):")
        else:
            suggested_dt = date_obj.replace(hour=12, minute=0)
            print("  Transistor publish date / time (original air date):")
        transistor_dt = prompt_datetime(suggested_dt)
    else:
        # Already have transistor_dt from the early prompt; apply last+1min logic retroactively
        if last_dt and last_dt.date() == transistor_dt.date() and transistor_dt == transistor_dt.replace(hour=transistor_dt.hour, minute=transistor_dt.minute):
            pass  # user already entered their preferred time

    # WordPress post date — always use current time (publish immediately)
    wp_dt = datetime.now()

    # ── Confirmation ──
    print("\n─── Summary ────────────────────────────────────────────────────────────────\n")
    print(f"  File:        {mp3_path.name}")
    desc_preview = (description[:72] + " …") if len(description) > 75 else description
    print(f"  Title:       {title}")
    print(f"  Description: {desc_preview or '(none)'}")
    print(f"  Tags:        {', '.join(tags) or '(none)'}")
    if category_name:
        print(f"  Category:    {category_name}")
    if not args.wp_only:
        print(f"  Transistor:  {transistor_dt.strftime('%Y-%m-%d %H:%M')} CT  → {TRANSISTOR_SHOW}")
    if not args.transistor_only:
        status_label = "draft" if args.draft else "published"
        print(f"  WordPress:   now ({wp_dt.strftime('%Y-%m-%d %H:%M')})  → {wp_url}  ({status_label})")
    if args.dry_run:
        print("\n  *** DRY-RUN — nothing will actually be posted ***")

    print()
    confirm = input("  Proceed? [Y/n] ").strip().lower()
    if confirm not in ("", "y", "yes"):
        print("  Aborted.")
        sys.exit(0)

    # ── Post to Transistor and WordPress ──
    transistor_ep_id: Optional[str] = None
    transistor_audio_url: Optional[str] = None
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
                transistor_ep_id, transistor_audio_url = post_to_transistor(
                    mp3_path, show_id, transistor_key,
                    title, description, tags, transistor_dt, dry_run=False,
                )
                save_last_transistor_dt(transistor_dt)

        if not args.transistor_only:
            if not wp_user or not wp_pass:
                print("\n  WordPress credentials not set — skipping.")
            else:
                print("\n─── WordPress ──────────────────────────────────────────────────────────────\n")
                _, wp_post_url = post_to_wordpress(
                    title, description, tags, wp_dt,
                    transistor_ep_id,
                    mp3_path,
                    wp_url, wp_user, wp_pass,
                    args.dry_run, args.draft,
                    category_name=category_name,
                    transistor_audio_url=transistor_audio_url,
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
