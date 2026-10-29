#!/usr/bin/env python3
"""
backfill_tags.py — Apply AUTO_TAG_RULES to all existing Transistor Archive episodes.

Fetches every published episode from The Ticket Archive (show 497), checks each
title against the same AUTO_TAG_RULES used in segment_post.py, and patches any
episode that is missing tags the rules would inject.

Usage:
    python3 backfill_tags.py              # live run (patches Transistor)
    python3 backfill_tags.py --dry-run    # preview only, no changes
"""

import os, json, re, time, sys, urllib.request, urllib.parse
from pathlib import Path
from collections import defaultdict
from typing import Optional

# ── Credentials ────────────────────────────────────────────────────────────────

ENV_FILE = Path("~/.segment_post_env").expanduser()
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

API_KEY = os.environ.get("TRANSISTOR_API_KEY", "")
if not API_KEY:
    sys.exit("TRANSISTOR_API_KEY not set. Add it to ~/.segment_post_env")

SHOW_ID   = "497"              # The Ticket Archive
API_BASE  = "https://api.transistor.fm/v1"
RATE_SLEEP = 1.5               # seconds between API calls

DRY_RUN = "--dry-run" in sys.argv or "-n" in sys.argv

# ── Auto-tag rules (keep in sync with segment_post.py AUTO_TAG_RULES) ──────────
# Each entry: (pattern_or_keywords, [tags_to_add])
# Pattern can be:
#   - a plain list of substrings (any match → tags applied)
#   - a compiled regex (re.search used for precise word-boundary matching)

AUTO_TAG_RULES = [
    (["trifecta"],
     ["Donovan Lewis", "trifecta"]),

    (["the rant revival", "c.u.n.t.", "cunt"],
     ["Gordon Keith"]),

    (["fake", "touchdown bros", "marge schwimdiddler", "dirty doug out",
      "skinlonnen", "friday funnies"],
     ["840 bit"]),

    (["wtds"],
     ["wtds"]),

    (["odeck", "observation deck"],
     ["observation deck"]),

    (["ebrake"],
     ["ebrake"]),

    (["birdhouse"],
     ["Birdhouse", "Corby Davidson"]),

    (["afternoon jub"],
     ["afternoon jub", "Afternoon Jub", "George Dunham"]),

    (["rent free"],
     ["Rent Free"]),

    (["nit brake"],
     ["nit brake"]),

    (["ham radio"],
     ["ham radio"]),

    (["snake pit"],
     ["snake pit"]),

    # Use regex so "friday fun" matches "Friday Fun" / "Friday Fun Jr"
    # but NOT "Friday Funnies" (word boundary after "fun").
    (re.compile(r"\bfriday fun\b"),
     ["friday fun"]),

    (["friday frivolity"],
     ["friday frivolity"]),

    (["bobs world"],
     ["bobs world"]),
]


def tags_for_title(title: str) -> list[str]:
    """Return tags that should be added based on the episode title."""
    lower = title.lower()
    result: list[str] = []
    for pattern, add_tags in AUTO_TAG_RULES:
        matched = False
        if isinstance(pattern, re.Pattern):
            matched = bool(pattern.search(lower))
        else:
            matched = any(kw in lower for kw in pattern)
        if matched:
            for t in add_tags:
                if t not in result:
                    result.append(t)
    return result


def segment_name(title: str) -> str:
    """Extract the segment portion from 'Segment - Show Date' titles."""
    for sep in (" - ", " – "):
        idx = title.rfind(sep)
        if idx > 0:
            return title[:idx].strip()
    return title.strip()


# ── API helpers ────────────────────────────────────────────────────────────────

def _req(method: str, path: str, fields: Optional[dict] = None) -> dict:
    url = API_BASE + path
    data = urllib.parse.urlencode(fields).encode() if fields else None
    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            time.sleep(RATE_SLEEP)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = 12 * (attempt + 1)
                print(f"    Rate limited — waiting {wait}s…")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Max retries exceeded")


def get_episodes_page(page: int) -> dict:
    return _req("GET", f"/episodes?show_id={SHOW_ID}"
                        f"&pagination[page]={page}"
                        f"&pagination[per]=50"
                        f"&status=published")


def patch_episode(ep_id: str, keywords: str) -> None:
    _req("PATCH", f"/episodes/{ep_id}",
         {"episode[keywords]": keywords})


# ── Main ───────────────────────────────────────────────────────────────────────

UNKNOWN_CACHE = Path("~/ticket-recorder/backfill_unknowns.json").expanduser()


def main() -> None:
    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Fetching episodes from The Ticket Archive (show {SHOW_ID})…\n")

    # Collect all episodes
    all_eps: list[dict] = []
    page = 1
    while True:
        resp = get_episodes_page(page)
        all_eps.extend(resp["data"])
        total_pages = resp["meta"]["totalPages"]
        print(f"  Page {page}/{total_pages} — {len(all_eps)} episodes fetched")
        if page >= total_pages:
            break
        page += 1

    print(f"\nTotal: {len(all_eps)} episodes\n")

    updated = skipped = errors = 0
    # Track unmatched segment names → {seg_name_lower: [{"id", "title"}, ...]}
    unmatched: dict = defaultdict(list)

    for ep in all_eps:
        ep_id  = ep["id"]
        attrs  = ep["attributes"]
        title  = (attrs.get("title") or "").strip()
        raw_kw = (attrs.get("keywords") or "").strip()

        # Parse existing tags (preserve original casing for non-new tags)
        existing_list = [t.strip() for t in raw_kw.split(",") if t.strip()]
        existing_lower = {t.lower() for t in existing_list}

        # Which tags should this episode have?
        should_add = tags_for_title(title)
        missing    = [t for t in should_add if t.lower() not in existing_lower]

        # Track episodes where NO rule fired (potential new tags)
        if not should_add:
            seg = segment_name(title)
            unmatched[seg.lower()].append({"id": ep_id, "title": title})

        if not missing:
            skipped += 1
            continue

        new_keywords = ", ".join(existing_list + missing)
        print(f"  [{ep_id}] {title[:65]}")
        print(f"    + {missing}")

        if not DRY_RUN:
            try:
                patch_episode(ep_id, new_keywords)
                updated += 1
            except Exception as exc:
                print(f"    ERROR: {exc}")
                errors += 1
        else:
            updated += 1   # count as "would update" in dry-run

    label = "Would update" if DRY_RUN else "Updated"
    print(f"\n── Done ────────────────────────────────────────────────────────────")
    print(f"  {label}:  {updated}")
    print(f"  Already correct: {skipped}")
    if errors:
        print(f"  Errors:          {errors}")

    # Save recurring unmatched segments (count ≥2) for review
    recurring = {
        name: entries
        for name, entries in unmatched.items()
        if len(entries) >= 2
    }
    # Sort by count desc
    recurring_sorted = dict(
        sorted(recurring.items(), key=lambda x: -len(x[1]))
    )
    UNKNOWN_CACHE.write_text(json.dumps(recurring_sorted, indent=2))
    print(f"\n── Unknown recurring segments (≥2 occurrences) saved to:")
    print(f"  {UNKNOWN_CACHE}")
    print(f"  {len(recurring_sorted)} unique segment names across "
          f"{sum(len(v) for v in recurring_sorted.values())} episodes")
    print(f"\nTop 20 by frequency:")
    for name, entries in list(recurring_sorted.items())[:20]:
        print(f"  {len(entries):>4}x  {entries[0]['title'][:60]}")


if __name__ == "__main__":
    main()
