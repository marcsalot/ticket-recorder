#!/usr/bin/env python3
"""
segment_post.py — Post an audio segment to Transistor.fm and WordPress.
=======================================================================
Interactively selects an MP3 from the media root (default: ~/Dropbox/The Ticket),
prompts for metadata, and publishes to:
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
    TICKET_MEDIA_DIR   (default: ~/Dropbox/The Ticket — set to ~/TheTicket to use Google Drive)
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
# Vault episodes post to the main Archive show with a "Vault" tag; the website builds
# the Vault podcast feed from that tag (https://theticketarchive.net/api/feed/vault).
VAULT_TAG = "Vault"
WP_URL_DEFAULT      = "https://unticket.com"
# Override with TICKET_MEDIA_DIR in ~/.segment_post_env to switch sources.
# e.g. TICKET_MEDIA_DIR=~/TheTicket  → use the Google Drive-synced local copy
DROPBOX_ROOT        = Path("~/Dropbox/The Ticket").expanduser()
SKIP_FOLDERS        = {"recordings", "edited"}  # subfolders excluded from the segment picker
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
    "bad radio":         ["BaD Radio", "BaD", "Bob Sturm", "Dan McDowell", "Donovan Lewis", "KTCK"],
    "bad":               ["BaD Radio", "BaD", "Bob Sturm", "Dan McDowell", "Donovan Lewis", "KTCK"],
    "sweet spot":        ["The Sweet Spot", "Sweet Spot", "Sean Bass", "Mino", "Monty", "KTCK"],
    "invasion":          ["The Invasion", "Invasion", "Donovan Lewis", "Matt McClearin", "KTCK"],
    "work in progress":  ["Work in Progress", "WIP", "DJ", "Monty", "KTCK"],
    "rant":               ["The Rant", "Rant", "Gordon Keith", "Dave Lane", "Corby Davidson", "KTCK"],
}
DEFAULT_TAGS = ["The Ticket", "KTCK"]

# Filename substring → replacement title (case-insensitive)
FILENAME_ALIASES: dict[str, str] = {
    "odeck":   "Observation Deck",
    "cunt":    "C.U.N.T.",          # CUNT → C.U.N.T. in post title
    r"\bFF\b": "Friday Fun",        # FF → Friday Fun (standalone abbreviation)
    r"\bGYM\b": "Generic Youth Minister",  # GYM → Generic Youth Minister
    r"\bCQH\b": "Community Quick Hits",      # CQH → Community Quick Hits
    r"\bEnews\b": "E-News",         # Enews → E-News in post title
}

# Filename/title substrings → (tags_to_add, tags_to_remove)
# Each entry: ([keywords_to_match], [tags_to_inject], [tags_to_drop])
AUTO_TAG_RULES: list[tuple[list[str], list[str], list[str]]] = [
    (["trifecta"],                                        ["Donovan Lewis", "trifecta"],  []),
    (["the rant revival", "c.u.n.t.", "cunt"],           ["Gordon Keith"],   []),
    (["fake", "touchdown bros", "marge schwimdiddler", "dirty doug out", "skinlonnen"],   ["840 bit"],        []),
    (["skinlonnen"],                                        ["skinlonnen", "840 bit"],        []),
    (["myrtle"],                                            ["myrtle", "840 bit"],            []),
    (["sgt buck maverick", "buck maverick"],                ["sgt buck maverick", "840 bit"], []),
    (["fake jerry", "fake jerry jones"],                    ["fake jerry jones", "840 bit"],          []),
    (["fake jason kidd"],                                   ["fake jason kidd", "840 bit"],           []),
    (["fake followill", "fake mark followill"],             ["fake followill", "840 bit"],            []),
    (["fake schottenheimer", "fake brian schottenheimer"],  ["fake brian schottenheimer", "840 bit"], []),
    (["fake stephen jones"],                                ["fake stephen jones", "840 bit"],        []),
    (["fake tiger", "fake tiger woods"],                    ["fake tiger", "840 bit"],               []),
    (["fake nolan ryan"],                                   ["fake nolan ryan", "840 bit"],          []),
    (["fake nico harrison"],                                ["fake nico harrison", "840 bit"],       []),
    (["fake michael irvin"],                                ["fake michael irvin", "840 bit"],       []),
    (["fake mike mccarthy", "fake mike mc"],                ["fake mike mccarthy", "840 bit"],       []),
    (["fake mack brown"],                                   ["fake mack brown", "840 bit"],          []),
    (["fake luka"],                                         ["fake luka", "840 bit"],                []),
    (["fake greg olsen"],                                   ["fake greg olsen", "840 bit"],          []),
    (["fake dusty may"],                                    ["fake dusty may", "840 bit"],           []),
    (["fake elon musk"],                                    ["fake elon musk", "840 bit"],           []),
    (["fake hockey"],                                       ["fake hockey show", "840 bit"],         []),
    (["fake cooper flagg"],                                 ["fake cooper flagg", "840 bit"],        []),
    (["fake bruce bochy"],                                  ["fake bruce bochy", "840 bit"],         []),
    (["veruca jones"],                                      ["veruca jones", "840 bit"],             []),
    (["marge schwimdiddler", "marge"],                       ["marge schwimdiddler", "840 bit"],       []),
    (["dirty doug out"],                                    ["dirty doug out", "840 bit"],           []),
    (["gordos corner", "gordo's corner", "gordo's corner"], ["gordos corner", "840 bit"],            []),
    (["political ad", "fake political"],                    ["fake political ads", "840 bit"],       []),
    (["wtds"],                                            ["wtds"],           []),
    (["odeck", "o-deck", "observation deck"],             ["observation deck"], []),
    (["ebrake", "e-brake"],                               ["ebrake"],           []),
    (["enews", "e-news"],                                 ["e-news"],            []),
    (["scattershooting"],                                 ["scattershooting"],    []),
    (["concert calendar"],                                ["concert calendar"],   []),
    (["muse in the news"],                                ["muse in the news"],   []),
    (["birdhouse", "the bird house"],                     ["birdhouse", "Corby Davidson"], []),
    (["afternoon jub"],                                    ["Afternoon Jub", "George Dunham"], []),
    (["rent free"],                                        ["Rent Free"], []),
    (["nit brake"],                                        ["nit brake", "840 bit"], []),
    (["ham radio"],                                        ["ham radio"], []),
    (["snake pit"],                                        ["snake pit"], []),
    (["friday fun"],                                       ["friday fun"], []),
    (["friday frivolity"],                                 ["friday frivolity", "840 bit"], []),
    (["bobs world", "bob's world", "bob’s world"],         ["bobs world"],   []),
    (["am i the jerk"],                                    ["am i the jerk"], []),
    (["you're an ass if", "you are an ass if", "ass if"],   ["you're an ass if"], []),
    (["boy music"],                                        ["boy music"],     []),
    (["gordo goes wireless"],                              ["gordo goes wireless", "840 bit"], []),
    (["the ramp"],                                         ["the ramp"],      []),
    (["daveland"],                                         ["daveland"],      []),
    (["chris chris"],                                      ["chris chris", "840 bit"],   []),
    (["carlton maxwell"],                                  ["carlton maxwell", "840 bit"], []),
    (["jesse the usher"],                                  ["jesse the usher", "840 bit"], []),
    (["hardline helpline"],                                ["hardline helpline"], []),
    (["generic youth minister"],                           ["generic youth minister", "840 bit"], []),
    (["audio depot"],                                      ["audio depot"],   []),
    (["deep throat informant", "deep throat"],            ["deep throat informant", "840 bit"], []),
    (["show & tell", "show and tell"],                     ["show & tell"],   []),
    (["elmer wayne"],                                      ["elmer wayne", "840 bit"],   []),
    (["ribby paultz", "ribby"],                            ["ribby paultz", "840 bit"],  []),
    (["shorty defazio"],                                   ["shorty defazio", "840 bit"], []),
    (["ticket mouse"],                                     ["ticket mouse", "840 bit"],  []),
    (["randy cody"],                                       ["randy cody", "840 bit"],    []),
    (["the list"],                                         ["the list"],      []),
    (["guy sportts"],                                      ["guy sportts", "840 bit"],   []),
    (["greg rosengarden"],                                 ["greg rosengarden", "840 bit"], []),
    (["johnny pants"],                                     ["johnny pants", "840 bit"],  []),
    (["mushmouth"],                                        ["mushmouth", "840 bit"],     []),
    (["fun time for the disabled"],                        ["fun time for the disabled", "840 bit"], []),
    (["willy tubbs"],                                      ["willy tubbs", "840 bit"],   []),
    (["steve in the attic"],                               ["steve in the attic", "840 bit"], []),
    (["junior's book club", "junior’s book club"],        ["craig's book club"], []),  # merged into Craig's Book Club
    (["sweet spot cinema"],                                ["sweet spot cinema"], []),
    (["big tex"],                                          ["big tex"],       []),
    (["craig's book club", "craig’s book club", "craigs book club"], ["craig's book club"], []),
    (["pat green"],                                        ["pat green"],      []),
    (["darndest", "people say"],                           ["people say the darndest things"], []),
    (["30th anniversary"],                                 ["30th anniversary"], []),
    (["sports panties"],                                   ["sports panties"], []),
    (["norm & the goose", "norm and the goose"],           ["norm & the goose", "840 bit"], []),
    (["michael irvin school"],                             ["michael irvin school closings", "840 bit"], []),
    (["hints from heloise"],                               ["hints from heloise", "840 bit"], []),
    (["the crazy prospector"],                             ["the crazy prospector", "840 bit"], []),
    (["dog talk"],                                         ["dog talk"],       []),
    (["sam gannon"],                                       ["sam gannon", "840 bit"],     []),
    (["clarence the maintenance", "clarence"],             ["clarence the maintenance man", "840 bit"], []),
    (["road trip talk"],                                   ["road trip talk"], []),
    (["ticketstock"],                                      ["ticketstock"],    []),
    (["dale hansen"],                                      ["dale hansen"],    []),
    (["ashrita furman"],                                   ["ashrita furman"], []),
    (["slapshot brothers"],                                ["slapshot brothers", "840 bit"], []),
    (["koko the gorilla"],                                 ["koko the gorilla", "840 bit"], []),
    (["musical musers"],                                   ["musical musers"], []),
    (["infrastructure man"],                               ["infrastructure man", "840 bit"], []),
    (["gas cap louie"],                                    ["gas cap louie", "840 bit"],  []),
    (["white elephant"],                                   ["white elephant"], []),
    (["craig goes to new mexico"],                         ["craig goes to new mexico"], []),
    (["hamdom drops"],                                     ["hamdom drops"],   []),
    (["valentines day talk"],                              ["valentines day talk"], []),
    (["super bowl"],                                       ["super bowl"],     []),
    (["summer camp talk"],                                 ["summer camp talk"], []),
    (["ham play by play"],                                 ["ham play by play"], []),
    (["ham vs food"],                                      ["ham vs food"],    []),
    (["crazy prospector"],                                 ["the crazy prospector", "840 bit"], []),
    (["ice cube"],                                         ["ice cube"],       []),
    (["zippy the clown"],                                  ["zippy the clown", "840 bit"], []),
    (["big jim jack"],                                     ["big jim jack", "840 bit"],   []),
    (["jubcation"],                                        ["jubcation"],      []),
    (["wip around"],                                       ["wip around"],     []),
    (["stumpin"],                                          ["stumpin"],        []),
    (["campound"],                                         ["campound"],       []),
    (["tributes to norm"],                                 ["tributes to norm"], []),
    (["halloween talk"],                                   ["halloween talk"], []),
    (["state fair talk"],                                  ["state fair talk"], []),
    (["frank caliendo"],                                   ["frank caliendo"], []),
    (["rhett miller"],                                     ["rhett miller"],   []),
    (["nit break"],                                        ["nit brake"],      []),  # spelling variant
    (["soaking wednesday"],                                ["soaking wednesday"], []),
    (["public restroom talk"],                             ["public restroom talk"], []),
    (["training camp"],                                    ["training camp"],  []),
    (["santa claus"],                                      ["fake santa claus", "840 bit"], []),
    (["junior xmas"],                                      ["junior xmas stories"], []),
    (["mike rhyner"],                                      ["Mike Rhyner"],    []),
    (["greg williams"],                                    ["Greg Williams"],  []),
    (["dan mcdowell"],                                     ["Dan McDowell"],   []),
    (["cqh", "community quick hits"],                      ["community quick hits"], []),
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


def find_transistor_show(api_key: str, show_name: str = TRANSISTOR_SHOW) -> str:
    """Return show ID for show_name, paginating if needed."""
    page = 1
    while True:
        resp = t_get("/shows", api_key,
                     {"pagination[page]": page, "pagination[per]": 20})
        for show in resp.get("data", []):
            if show.get("attributes", {}).get("title", "").strip() == show_name:
                return show["id"]
        if page >= resp.get("meta", {}).get("totalPages", 1):
            break
        page += 1
    raise RuntimeError(f"Transistor show '{show_name}' not found")


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


def _scandir_retry(directory: Path, max_attempts: int = 5) -> list:
    """os.scandir with per-directory retry on EINTR (errno 4).

    Dropbox's FUSE driver can interrupt scandir mid-call; retrying at the
    individual directory level is far cheaper than restarting the whole walk.
    """
    for attempt in range(max_attempts):
        try:
            with os.scandir(directory) as it:
                return list(it)
        except (InterruptedError, OSError) as exc:
            # Only retry on EINTR; propagate all other OS errors immediately.
            if not isinstance(exc, InterruptedError) and getattr(exc, 'errno', None) != 4:
                raise
            if attempt < max_attempts - 1:
                time.sleep(1 + attempt)
            else:
                raise RuntimeError(
                    f"Could not scan {directory} after {max_attempts} attempts"
                ) from exc
    return []  # unreachable


def scan_mp3_files(root: Path) -> list[Path]:
    """Return MP3 files under root, sorted newest-first by mtime.

    Primary: subprocess `find` — a native binary that handles EINTR transparently
    at the OS level, unaffected by Python’s FUSE bindings or PEP-475 gaps.
    Fallback: manual os.scandir walk with per-directory EINTR retry.
    """
    import subprocess

    if not root.exists():
        return []

    # — Primary: system find —
    try:
        result = subprocess.run(
            ["find", str(root), "-name", "*.mp3", "-type", "f"],
            capture_output=True, text=True, timeout=120,
        )
        # find may print partial results even on a non-zero exit (e.g. permission denied
        # on one subdir); use whatever it found as long as there's some output.
        if result.stdout.strip():
            files = [Path(line) for line in result.stdout.splitlines() if line.strip()]
            return sorted(files, key=_mtime_safe, reverse=True)
        if result.returncode != 0:
            log.warning("find exited %d: %s — falling back to Python walk",
                        result.returncode, result.stderr.strip()[:200])
    except Exception as exc:
        log.warning("find failed (%s) — falling back to Python walk", exc)

    # — Fallback: os.scandir walk with per-directory EINTR retry —
    found: list[Path] = []

    def _walk(directory: Path) -> None:
        try:
            entries = _scandir_retry(directory)
        except Exception as exc:
            log.warning("Skipping directory %s: %s", directory, exc)
            return
        subdirs: list[Path] = []
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith('.mp3'):
                    found.append(Path(entry.path))
                elif entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.'):
                    subdirs.append(Path(entry.path))
            except OSError:
                pass
        for subdir in subdirs:
            _walk(subdir)

    _walk(root)
    return sorted(found, key=_mtime_safe, reverse=True)


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


def reset_custom_tags(show_name: str) -> None:
    """Remove a show's saved override so the built-in defaults apply again."""
    custom = load_custom_tags()
    custom.pop(show_name, None)
    CUSTOM_TAGS_FILE.write_text(json.dumps(custom, indent=2))


def apply_tag_edits(tags: list[str], raw: str) -> list[str]:
    """Apply a comma-separated '+add, -remove' edit line to a tag list."""
    tags = list(tags)
    for part in raw.split(","):
        part = part.strip()
        if part.startswith("+"):
            t = part[1:].strip()
            if t and t.lower() not in (x.lower() for x in tags):
                tags.append(t)
        elif part.startswith("-"):
            t = part[1:].strip().lower()
            tags = [x for x in tags if x.lower() != t]
        elif part and part.lower() not in (x.lower() for x in tags):   # bare word → add
            tags.append(part)
    return tags


def edit_default_tags(show_arg: Optional[str] = None) -> None:
    """Interactive editor for per-show default tags (no posting)."""
    custom = load_custom_tags()
    builtin = sorted({k.title() for k in SHOW_DEFAULT_TAGS} - {s.lower().title() for s in custom})
    shows = sorted(custom) + [s for s in builtin if s not in custom]

    print("\n─── Default tags per show ──────────────────────────────────────────────────\n")
    if show_arg:
        show = next((s for s in shows if s.lower() == show_arg.lower()), show_arg)
    else:
        for i, s in enumerate(shows, 1):
            mark = "saved" if s in custom else "built-in"
            print(f"  {i:>2}. {s}  ({mark})")
            print(f"      {', '.join(default_tags_for(s))}")
        raw = input("\n  Show number or name (Enter to quit): ").strip()
        if not raw:
            return
        show = shows[int(raw) - 1] if raw.isdigit() and 0 < int(raw) <= len(shows) else raw

    while True:
        current = default_tags_for(show)
        mark = "saved" if show in load_custom_tags() else "built-in"
        print(f"\n  {show} ({mark}):")
        print(f"    {', '.join(current)}")
        raw = input("  Edit (+add / -remove, 'reset' = built-in defaults, Enter = done): ").strip()
        if not raw:
            return
        if raw.lower() == "reset":
            reset_custom_tags(show)
            print("  Reset to built-in defaults.")
            continue
        new = apply_tag_edits(current, raw)
        if new != current:
            save_custom_tags(show, new)
            print("  Saved.")


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
    base = default_tags_for(show_name)          # what's actually saved for the show
    defaults = list(base)
    auto_added: list[str] = []
    if extra_tags:
        for t in extra_tags:
            if t not in defaults:
                defaults.append(t)
                auto_added.append(t)
    if remove_tags:
        rl = [r.lower() for r in remove_tags]
        defaults = [t for t in defaults if t.lower() not in rl]
    saved_marker = " (saved)" if show_name in load_custom_tags() else ""
    print(f"\n  Tags ({show_name or 'general'}{saved_marker}):")
    print(f"    {', '.join(defaults)}")
    raw = prompt("  Edit (+add / -remove, Enter to keep)", "")
    if not raw.strip():
        return list(defaults)
    tags = apply_tag_edits(defaults, raw)
    print(f"  Tags: {', '.join(tags)}")
    # Offer to save the *user's* edits as the show default. Auto-tags (from
    # AUTO_TAG_RULES matching this file) are per-post and never saved, and
    # auto-removals don't strip saved defaults.
    new_default = apply_tag_edits(base, raw)
    auto_l = {a.lower() for a in auto_added}
    explicitly_added = {p.strip().lstrip("+").strip().lower() for p in raw.split(",") if not p.strip().startswith("-")}
    new_default = [t for t in new_default if t.lower() not in auto_l or t.lower() in explicitly_added]
    if new_default != base and show_name:
        print(f"  New default for '{show_name}': {', '.join(new_default)}")
        save_q = input("  Save as default? [y/N] ").strip().lower()
        if save_q in ("y", "yes"):
            save_custom_tags(show_name, new_default)
            print("  Saved.")
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
    parser.add_argument("--vault", action="store_true",
                        help="Vault mode: post to The Ticket Archive on the original air date with the Vault tag "
                             "(the site builds the Vault feed from that tag). Skips WordPress.")
    parser.add_argument("--tags", nargs="?", const="", metavar="SHOW",
                        help="Edit default tags per show (no posting). Optional show name.")
    args = parser.parse_args()

    if args.tags is not None:
        edit_default_tags(args.tags or None)
        return

    # ── Vault mode: same Archive show (original air date), tagged Vault, no WordPress ──
    active_show = TRANSISTOR_SHOW
    if args.vault and not args.wp_only:
        args.transistor_only = True   # archival audio is podcast-only

    setup_logging()
    log.info("─── segment_post started ───")

    # ── Load credentials ──
    load_env()

    # Allow overriding the media root via env (e.g. switch to Google Drive copy)
    global DROPBOX_ROOT
    _media_dir = os.environ.get("TICKET_MEDIA_DIR", "").strip()
    if _media_dir:
        DROPBOX_ROOT = Path(_media_dir).expanduser()

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
    if args.vault:
        print("\n╔══════════════════════════════════════════╗")
        print("║   ⭐ Segment Post — THE TICKET VAULT ⭐  ║")
        print("║   Archive feed (air date) + Vault feed   ║")
        print("╚══════════════════════════════════════════╝")
    else:
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
        all_files = scan_mp3_files(DROPBOX_ROOT)
        # Exclude full-episode and staging folders from the segment picker
        files = [
            p for p in all_files
            if not any(
                part.lower() in SKIP_FOLDERS
                for part in p.relative_to(DROPBOX_ROOT).parts
            )
        ]
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
    # Auto-tag with the air year so episodes are browsable by year
    if date_obj:
        year_str = str(date_obj.year)
        if year_str not in _add_tags:
            _add_tags.append(year_str)
    if args.vault:
        if VAULT_TAG.lower() not in [x.lower() for x in _add_tags]:
            _add_tags.append(VAULT_TAG)
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
        if args.vault:
            # Vault: air date is already baked into the title for historical ordering.
            # Prompt separately for the actual Transistor publish date so the user can
            # schedule episodes to drip out rather than all going live immediately.
            print("  Transistor publish date / time (air date is in the title; Enter for now, or set a future date to schedule):")
            suggested_dt = datetime.now().replace(second=0, microsecond=0)
        elif last_dt and last_dt.date() == date_obj.date():
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
        # Vault with no filename date: air date was prompted early for the title;
        # now separately ask for the Transistor publish date.
        if args.vault:
            print()
            print("  Transistor publish date / time (air date is in the title; Enter for now, or set a future date to schedule):")
            transistor_dt = prompt_datetime(datetime.now().replace(second=0, microsecond=0))

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
        vault_flag = "  ⭐ VAULT" if args.vault else ""
        print(f"  Transistor:  {transistor_dt.strftime('%Y-%m-%d %H:%M')} CT  → {active_show}{vault_flag}")
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
                print(f"  [DRY-RUN] Would post to Transistor show '{active_show}'")
                transistor_ep_id = "dry-run-id"
            else:
                print(f"  Looking up show '{active_show}' …", flush=True)
                show_id = find_transistor_show(transistor_key, active_show)
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
