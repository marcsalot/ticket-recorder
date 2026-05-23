#!/usr/bin/env python3
"""
update_transistor_keywords.py
==============================
Retroactively sets keywords on all 'The Ticket Archive' Transistor episodes
based on show name and air date parsed from each episode title.

Host rules for The Hardline:
  >= 2020-01-06  : Corby Davidson, Bob Sturm, Dave Lane
  2008-01-10 to 2020-01-05 : Mike Rhyner, Corby Davidson
  < 2008-01-10   : Mike Rhyner, Corby Davidson, Greg Williams

Usage:
    python3 update_transistor_keywords.py --dry-run   # preview only
    python3 update_transistor_keywords.py             # apply changes
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
SHOW_NAME    = "The Ticket Archive"
API_BASE     = "https://api.transistor.fm/v1"
ENV_FILE     = Path("~/.segment_post_env").expanduser()
RATE_SLEEP   = 1.2   # seconds between API calls

# Date boundaries for Hardline hosts
RHYNER_LEFT  = date(2020, 1, 6)   # Mike Rhyner's last day
GRUBES_DIED  = date(2008, 1, 10)  # Greg Williams

# ── Default keywords by show keyword ─────────────────────────────────────────
# Matched by substring (lowercase) against the episode title.
# Hardline is handled separately with date logic.
SHOW_KEYWORDS: dict[str, list[str]] = {
    "musers":           ["The Musers", "Musers", "Gordon Keith", "George Dunham", "Craig Miller", "KTCK"],
    "sweet spot":       ["The Sweet Spot", "Sweet Spot", "Sean Bass", "Mino", "Monty", "KTCK"],
    "invasion":         ["The Invasion", "Invasion", "Donovan Lewis", "Matt McClearin", "KTCK"],
    "work in progress": ["Work in Progress", "WIP", "DJ", "Monty", "KTCK"],
    "wip":              ["Work in Progress", "WIP", "DJ", "Monty", "KTCK"],
    "bad radio":        ["BaD Radio", "BaD", "Bob Sturm", "Dan McDowell", "KTCK"],
    "the rant":         ["The Rant", "Rant", "Gordon Keith", "Dave Lane", "Corby Davidson", "KTCK"],
}
DEFAULT_KEYWORDS = ["The Ticket", "KTCK"]


def hardline_keywords(air_date: date | None) -> list[str]:
    base = ["The Hardline", "Hardline", "Corby Davidson"]
    if air_date is None or air_date >= RHYNER_LEFT:
        return base + ["Bob Sturm", "Dave Lane", "KTCK"]
    elif air_date >= GRUBES_DIED:
        return base + ["Mike Rhyner", "KTCK"]
    else:
        return base + ["Mike Rhyner", "Greg Williams", "KTCK"]


def keywords_for(title: str, air_date: date | None) -> list[str]:
    tl = title.lower()
    if "hardline" in tl:
        return hardline_keywords(air_date)
    for keyword, tags in SHOW_KEYWORDS.items():
        if keyword in tl:
            return list(tags)
    return list(DEFAULT_KEYWORDS)


def parse_air_date(ep: dict) -> date | None:
    """Parse air date from published_at, falling back to title date (M.D.YYYY)."""
    pub = ep.get("attributes", {}).get("published_at") or ""
    if pub:
        try:
            return datetime.fromisoformat(pub[:10]).date()
        except ValueError:
            pass
    # Fallback: look for trailing M.D.YYYY or MM.DD.YYYY in title
    title = ep.get("attributes", {}).get("title", "")
    import re
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$", title)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


# ── API helpers ───────────────────────────────────────────────────────────────

def _request(method: str, url: str, api_key: str, data: bytes | None = None) -> dict:
    headers = {"x-api-key": api_key, "Content-Type": "application/x-www-form-urlencoded"}
    for attempt in range(6):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 15 * (attempt + 1)
                print(f"  Rate limited — waiting {wait}s …")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}") from e
        except OSError as e:
            wait = 10 * (attempt + 1)
            print(f"  Network error (attempt {attempt+1}): {e} — retrying in {wait}s …")
            time.sleep(wait)
            continue
    raise RuntimeError("Max retries exceeded")


def t_get(path: str, api_key: str, params: dict | None = None) -> dict:
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    time.sleep(RATE_SLEEP)
    return _request("GET", url, api_key)


def t_patch(path: str, api_key: str, fields: dict) -> dict:
    time.sleep(RATE_SLEEP)
    return _request("PATCH", API_BASE + path, api_key,
                    data=urllib.parse.urlencode(fields).encode())


# ── Main ──────────────────────────────────────────────────────────────────────

def load_env() -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "TRANSISTOR_API_KEY":
                return v.strip().strip('"').strip("'")
    return os.environ.get("TRANSISTOR_API_KEY", "")


def find_show_id(api_key: str) -> str:
    page = 1
    while True:
        resp = t_get("/shows", api_key, {"pagination[per]": 20, "pagination[page]": page})
        for s in resp.get("data", []):
            if s["attributes"]["title"].strip() == SHOW_NAME:
                return s["id"]
        if page >= resp.get("meta", {}).get("totalPages", 1):
            break
        page += 1
    raise RuntimeError(f"Show '{SHOW_NAME}' not found")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update Transistor episode keywords.")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview only")
    parser.add_argument("--show-all", action="store_true", help="Print every episode, not just changes")
    args = parser.parse_args()

    api_key = load_env()
    if not api_key:
        sys.exit("TRANSISTOR_API_KEY not set in ~/.segment_post_env")

    print(f"Finding show '{SHOW_NAME}' …")
    show_id = find_show_id(api_key)
    print(f"Show ID: {show_id}")

    # Collect all episodes
    print("Loading all episodes …")
    episodes: list[dict] = []
    page = 1
    while True:
        resp = t_get("/episodes", api_key, {
            "show_id": show_id,
            "pagination[per]": 50,
            "pagination[page]": page,
        })
        batch = resp.get("data", [])
        if not batch:
            break
        episodes.extend(batch)
        total_pages = resp.get("meta", {}).get("totalPages", 1)
        print(f"  Page {page}/{total_pages}  ({len(episodes)} loaded)", end="\r", flush=True)
        if page >= total_pages:
            break
        page += 1

    print(f"\nLoaded {len(episodes)} episodes.")

    updated = skipped = errors = 0

    for ep in episodes:
        ep_id   = ep["id"]
        attrs   = ep.get("attributes", {})
        title   = attrs.get("title", "")
        cur_kw  = attrs.get("keywords") or ""
        cur_set = {k.strip() for k in cur_kw.split(",") if k.strip()}

        air_date = parse_air_date(ep)
        target   = keywords_for(title, air_date)
        target_set = set(target)

        if cur_set == target_set:
            if args.show_all:
                print(f"  SAME  {ep_id}  {title[:55]}")
            skipped += 1
            continue

        date_str = air_date.isoformat() if air_date else "????"
        kw_str   = ", ".join(target)
        print(f"  {'DRY ' if args.dry_run else 'UPD '} {ep_id}  [{date_str}]  {title[:45]}")
        print(f"        was: {cur_kw[:70] or '(empty)'}")
        print(f"        now: {kw_str}")

        if not args.dry_run:
            try:
                t_patch(f"/episodes/{ep_id}", api_key, {"episode[keywords]": kw_str})
                updated += 1
            except Exception as exc:
                print(f"        ERROR: {exc}")
                errors += 1
        else:
            updated += 1

    label = "Would update" if args.dry_run else "Updated"
    print(f"\n{'─'*60}")
    print(f"  {label}: {updated}  |  Already correct: {skipped}  |  Errors: {errors}")
    if args.dry_run:
        print("  Run without --dry-run to apply.")


if __name__ == "__main__":
    main()
