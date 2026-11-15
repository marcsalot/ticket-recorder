#!/usr/bin/env python3
"""
1310cloud_size_check.py — Estimate download size for 1310.cloud media not yet on
                          Transistor "The Ticket Archive".

Steps:
  1. Scrape all pages of https://1310.cloud/api/playlist (parallelised)
  2. Load all episode titles from Transistor "The Ticket Archive"
  3. Cross-reference — find items not already posted
  4. HTTP HEAD each missing MP3 (parallelised) to get Content-Length
  5. Report total and save detail to JSON

Usage:
    python3 1310cloud_size_check.py
    python3 1310cloud_size_check.py --max-pages 5     # quick test
    python3 1310cloud_size_check.py --no-transistor   # size everything (no cross-ref)
    python3 1310cloud_size_check.py --output out.json
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────

ENV_FILE            = Path("~/.segment_post_env").expanduser()
TRANSISTOR_API_BASE = "https://api.transistor.fm/v1"
TRANSISTOR_SHOW     = "The Ticket Archive"
PLAYER_BASE         = "https://1310.cloud/api/playlist"

TRANSISTOR_RATE_SLEEP = 1.5   # seconds between Transistor API calls (~6/10s, under limit)
TRANSISTOR_RETRY_WAIT = 12    # seconds to wait after a 429 before retrying
CLOUD_WORKERS         = 8     # concurrent 1310.cloud page fetches
CLOUD_PAGE_SLEEP      = 0.15  # polite delay per worker between pages
HEAD_WORKERS          = 30    # concurrent S3 HEAD requests
CLOUD_TIMEOUT         = 30    # seconds per page fetch
HEAD_TIMEOUT          = 20    # seconds per HEAD request

# Browser-like User-Agent so 1310.cloud / Cloudflare doesn't return 403
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")

# ── Regex patterns ────────────────────────────────────────────────────────────

# Match only the PLAY button (has btn-play class) to avoid duplicating
# the same URL from the "Add to Queue" dropdown.
_MEDIA_RE = re.compile(
    r'btn-play[^>]*\s+data-player-id-param="\d+"'
    r'[^>]*data-player-title-param="([^"]*)"'
    r'[^>]*data-player-url-param="(https://[^"]+\.mp3)"',
    re.DOTALL,
)

# ── Env loading ───────────────────────────────────────────────────────────────

def load_env() -> None:
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


# ── Transistor helpers ────────────────────────────────────────────────────────

def _t_request(url: str, api_key: str) -> dict:
    headers = {"x-api-key": api_key}
    for attempt in range(6):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = TRANSISTOR_RETRY_WAIT * (attempt + 1)
                print(f"  [Transistor] 429 — waiting {wait}s (attempt {attempt+1}) …",
                      flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"Transistor HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}"
            ) from exc
        except OSError as exc:
            if attempt < 5:
                time.sleep(5)
                continue
            raise
    raise RuntimeError("Transistor: max retries exceeded")


def t_get(path: str, api_key: str, params: Optional[dict] = None) -> dict:
    url = TRANSISTOR_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    time.sleep(TRANSISTOR_RATE_SLEEP)
    return _t_request(url, api_key)


def find_show_id(api_key: str) -> str:
    page = 1
    while True:
        resp = t_get("/shows", api_key, {"pagination[page]": page, "pagination[per]": 20})
        for show in resp.get("data", []):
            if show.get("attributes", {}).get("title", "").strip() == TRANSISTOR_SHOW:
                return show["id"]
        if page >= resp.get("meta", {}).get("totalPages", 1):
            break
        page += 1
    raise RuntimeError(f"Show '{TRANSISTOR_SHOW}' not found on Transistor")


def load_transistor_titles(show_id: str, api_key: str) -> set[str]:
    """Return lowercased set of all episode titles from Transistor."""
    titles: set[str] = set()
    page = 1
    while True:
        resp = t_get("/episodes", api_key, {
            "show_id": show_id,
            "pagination[page]": page,
            "pagination[per]": 50,
        })
        for ep in resp.get("data", []):
            t = ep.get("attributes", {}).get("title", "").strip()
            if t:
                titles.add(t.lower())
        meta = resp.get("meta", {})
        total_pages = meta.get("totalPages", 1)
        total_eps   = meta.get("totalCount", "?")
        print(f"  Transistor page {page}/{total_pages} "
              f"({len(titles)} titles, {total_eps} total)", flush=True)
        if page >= total_pages:
            break
        page += 1
    return titles


# ── 1310.cloud scraping ───────────────────────────────────────────────────────

def _parse_page_html(html_text: str) -> list[dict]:
    """Parse playlist HTML. Returns list of {title, url} items (one per play button)."""
    return [
        {"title": html.unescape(t), "url": u}
        for t, u in _MEDIA_RE.findall(html_text)
    ]


def _fetch_page_html(page: int) -> Optional[str]:
    url = f"{PLAYER_BASE}?p={page}"
    req = urllib.request.Request(url, headers={
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": _UA,
        "Accept": "text/html,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"  [1310.cloud] page {page} error: {exc}", flush=True)
        return None


def fetch_all_media(max_pages: Optional[int] = None) -> list[dict]:
    """
    Fetch all media items from 1310.cloud using a thread pool.
    Pages are fetched concurrently but results are assembled in order.
    We stop when we hit a page with no Next button.
    """
    all_items: list[dict] = []
    print_lock = Lock()

    # Phase 1: discover total pages via a sentinel scan
    # We'll use a producer/consumer approach: fetch a batch of pages,
    # check for the last page, then fetch more.
    BATCH = CLOUD_WORKERS * 4  # pages per batch
    page = 1
    done = False

    while not done:
        if max_pages:
            batch_end = min(page + BATCH - 1, max_pages)
        else:
            batch_end = page + BATCH - 1

        pages_to_fetch = list(range(page, batch_end + 1))
        results: dict[int, tuple[list[dict], bool]] = {}

        with ThreadPoolExecutor(max_workers=CLOUD_WORKERS) as pool:
            futures = {pool.submit(_fetch_page_html, p): p for p in pages_to_fetch}
            for fut in as_completed(futures):
                p = futures[fut]
                time.sleep(CLOUD_PAGE_SLEEP)
                html_text = fut.result()
                results[p] = _parse_page_html(html_text) if html_text else []

        # Assemble in page order; stop at the first empty page (end of data)
        for p in pages_to_fetch:
            items = results.get(p, [])
            if not items:
                done = True
                break
            all_items.extend(items)
            with print_lock:
                print(f"  Page {p:>5}: {len(items):>3} items  "
                      f"(running total: {len(all_items):,})", flush=True)
            if max_pages and p >= max_pages:
                done = True
                break

        page = batch_end + 1

    return all_items


# ── S3 HEAD requests ──────────────────────────────────────────────────────────

def _head_size(item: dict) -> tuple[dict, Optional[int]]:
    """Return (item, size_bytes) for one MP3 URL."""
    url = item["url"]
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=HEAD_TIMEOUT) as resp:
            cl = resp.headers.get("Content-Length")
            return item, int(cl) if cl else None
    except Exception:
        return item, None


def fetch_sizes(items: list[dict]) -> tuple[int, int]:
    """
    Issue HEAD requests for all items concurrently.
    Mutates each item in-place, adding 'size_bytes'.
    Returns (total_bytes, unknown_count).
    """
    # Use lists as mutable containers so the nested function can update them
    counters = {"bytes": 0, "unknown": 0, "done": 0}
    lock = Lock()

    def _on_done(item: dict, size: Optional[int]) -> None:
        item["size_bytes"] = size
        with lock:
            counters["done"] += 1
            if size is not None:
                counters["bytes"] += size
            else:
                counters["unknown"] += 1
            done = counters["done"]
            total = counters["bytes"]
        if done % 200 == 0 or done == len(items):
            print(f"  HEAD {done:>6}/{len(items):,}  "
                  f"total so far: {fmt_size(total)}", flush=True)

    with ThreadPoolExecutor(max_workers=HEAD_WORKERS) as pool:
        futures = {pool.submit(_head_size, it): it for it in items}
        for fut in as_completed(futures):
            item, size = fut.result()
            _on_done(item, size)

    return counters["bytes"], counters["unknown"]


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Estimate 1310.cloud download size vs Transistor 'The Ticket Archive'"
    )
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Limit to N pages from 1310.cloud (for testing)")
    ap.add_argument("--no-transistor", action="store_true",
                    help="Skip Transistor cross-ref; count all 1310.cloud items")
    ap.add_argument("--output", default="1310cloud_missing.json",
                    help="Output JSON file (default: 1310cloud_missing.json)")
    args = ap.parse_args()

    load_env()
    api_key = os.environ.get("TRANSISTOR_API_KEY", "")

    # ── Step 1: Load Transistor titles ────────────────────────────────────────
    transistor_titles: set[str] = set()
    if not args.no_transistor:
        if not api_key:
            print("ERROR: TRANSISTOR_API_KEY not set. "
                  "Add to ~/.segment_post_env or pass --no-transistor.", file=sys.stderr)
            sys.exit(1)
        print("━━━ Step 1/4: Loading Transistor episodes ━━━", flush=True)
        show_id = find_show_id(api_key)
        print(f"  Show ID: {show_id}", flush=True)
        transistor_titles = load_transistor_titles(show_id, api_key)
        print(f"  → {len(transistor_titles):,} titles loaded\n", flush=True)
    else:
        print("━━━ Step 1/4: Transistor cross-ref skipped (--no-transistor) ━━━\n",
              flush=True)

    # ── Step 2: Scrape 1310.cloud ─────────────────────────────────────────────
    print("━━━ Step 2/4: Scraping 1310.cloud ━━━", flush=True)
    t0 = time.time()
    all_media_raw = fetch_all_media(max_pages=args.max_pages)
    elapsed = time.time() - t0

    # Deduplicate by URL (play button + queue button both carry same attrs)
    seen_urls: set[str] = set()
    all_media: list[dict] = []
    for item in all_media_raw:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            all_media.append(item)
    print(f"  → {len(all_media_raw):,} raw / {len(all_media):,} unique items in {elapsed:.0f}s\n",
          flush=True)

    if not all_media:
        print("No media found — check network or try --max-pages 1 for a test.", file=sys.stderr)
        sys.exit(1)

    # ── Step 3: Cross-reference ───────────────────────────────────────────────
    print("━━━ Step 3/4: Cross-referencing ━━━", flush=True)
    if args.no_transistor:
        missing = all_media
        already = 0
    else:
        missing = [m for m in all_media
                   if m["title"].strip().lower() not in transistor_titles]
        already = len(all_media) - len(missing)

    print(f"  1310.cloud total  : {len(all_media):,}")
    print(f"  Already on Transistor : {already:,}")
    print(f"  Missing (to download) : {len(missing):,}\n", flush=True)

    if not missing:
        print("Nothing to download — all 1310.cloud items are already on Transistor!")
        _save(args.output, all_media, already, missing, 0, 0)
        return

    # ── Step 4: HEAD requests ─────────────────────────────────────────────────
    print(f"━━━ Step 4/4: Fetching file sizes ({len(missing):,} HEAD requests) ━━━",
          flush=True)
    t1 = time.time()
    total_bytes, unknown_count = fetch_sizes(missing)
    elapsed2 = time.time() - t1
    print(f"\n  HEAD requests done in {elapsed2:.0f}s\n", flush=True)

    # ── Report ────────────────────────────────────────────────────────────────
    _save(args.output, all_media, already, missing, total_bytes, unknown_count)

    sep = "━" * 52
    print(sep)
    print(f"  1310.cloud total items   : {len(all_media):,}")
    print(f"  Already on Transistor    : {already:,}")
    print(f"  To download              : {len(missing):,}")
    print(f"  Total download size      : {fmt_size(total_bytes)}")
    if unknown_count:
        print(f"  Files w/ unknown size    : {unknown_count:,}")
    print(f"  Results saved to         : {args.output}")
    print(sep)


def _save(output: str, all_media: list[dict], already: int,
          missing: list[dict], total_bytes: int, unknown_count: int) -> None:
    Path(output).write_text(json.dumps({
        "summary": {
            "total_1310cloud": len(all_media),
            "already_on_transistor": already,
            "missing_count": len(missing),
            "total_size_bytes": total_bytes,
            "total_size_human": fmt_size(total_bytes) if total_bytes else "0 B",
            "unknown_size_count": unknown_count,
        },
        "missing_items": missing,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
