#!/usr/bin/env python3
"""
download_redgifs.py - Download GIFs/videos from redgifs.com

Usage:
  # Download specific URLs
  python3 download_redgifs.py --url https://redgifs.com/watch/abc https://redgifs.com/watch/xyz

  # Download by search query
  python3 download_redgifs.py --search "funny cats" --count 10

  # Download all GIFs from a user
  python3 download_redgifs.py --user someusername

  # Specify output folder and quality
  python3 download_redgifs.py --search "dogs" --folder ~/Downloads/dogs --quality sd
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

import redgifs
from redgifs import Order


def extract_id_from_url(url: str) -> Optional[str]:
    """Extract the GIF ID from a redgifs.com URL."""
    # Handles: https://redgifs.com/watch/someid
    #          https://www.redgifs.com/watch/someid
    #          someid (bare ID)
    match = re.search(r"redgifs\.com/(?:watch|ifr)/([A-Za-z0-9]+)", url)
    if match:
        return match.group(1)
    # Bare ID (no URL)?
    if re.fullmatch(r"[A-Za-z0-9]+", url):
        return url
    return None


def safe_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    return re.sub(r'[^\w\-_. ]', '_', name)


def download_by_urls(api: redgifs.API, urls: List[str], folder: Path, quality: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for url in urls:
        gif_id = extract_id_from_url(url)
        if not gif_id:
            print(f"[SKIP] Could not parse ID from: {url}")
            continue
        try:
            gif = api.get_gif(gif_id)
            dl_url = gif.urls.hd if quality == "hd" and gif.urls.hd else gif.urls.sd
            if not dl_url:
                print(f"[SKIP] No downloadable URL for {gif_id}")
                continue
            dest = folder / f"{gif_id}.mp4"
            if dest.exists():
                print(f"[EXISTS] {dest}")
                continue
            print(f"Downloading {gif_id}...", end=" ", flush=True)
            api.download(dl_url, dest)
            print(f"-> {dest}")
        except Exception as e:
            print(f"[ERROR] {gif_id}: {e}")


def download_by_search(
    api: redgifs.API,
    query: str,
    folder: Path,
    quality: str,
    count: int,
    pages: int,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    total_downloaded = 0
    for page in range(1, pages + 1):
        try:
            result = api.search(query, order=Order.TRENDING, count=count, page=page)
        except Exception as e:
            print(f"[ERROR] Search failed on page {page}: {e}")
            break

        if not result.gifs:
            print(f"No more results at page {page}.")
            break

        print(f"Page {page}/{result.pages} — {len(result.gifs)} GIFs found (total: {result.total})")

        for gif in result.gifs:
            dl_url = gif.urls.hd if quality == "hd" and gif.urls.hd else gif.urls.sd
            if not dl_url:
                print(f"  [SKIP] No URL for {gif.id}")
                continue
            dest = folder / f"{gif.id}.mp4"
            if dest.exists():
                print(f"  [EXISTS] {gif.id}")
                continue
            try:
                print(f"  Downloading {gif.id}...", end=" ", flush=True)
                api.download(dl_url, dest)
                total_downloaded += 1
                print(f"done ({total_downloaded} total)")
            except Exception as e:
                print(f"[ERROR] {gif.id}: {e}")
            time.sleep(0.2)  # be polite

        if page >= result.pages:
            break

    print(f"\nDownloaded {total_downloaded} GIFs to {folder}")


def download_by_user(
    api: redgifs.API,
    username: str,
    folder: Path,
    quality: str,
    max_count: int,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    total_downloaded = 0
    page = 1

    while True:
        try:
            result = api.search_creator(username, page=page, count=80, order=Order.LATEST)
        except Exception as e:
            print(f"[ERROR] Failed to fetch page {page} for user '{username}': {e}")
            break

        gifs = result.gifs
        if not gifs:
            break

        print(f"Page {page} — {len(gifs)} GIFs")

        for gif in gifs:
            if max_count and total_downloaded >= max_count:
                print(f"Reached max count ({max_count}).")
                return

            dl_url = gif.urls.hd if quality == "hd" and gif.urls.hd else gif.urls.sd
            if not dl_url:
                print(f"  [SKIP] No URL for {gif.id}")
                continue
            dest = folder / f"{gif.id}.mp4"
            if dest.exists():
                print(f"  [EXISTS] {gif.id}")
                total_downloaded += 1
                continue
            try:
                print(f"  Downloading {gif.id}...", end=" ", flush=True)
                api.download(dl_url, dest)
                total_downloaded += 1
                print(f"done ({total_downloaded} total)")
            except Exception as e:
                print(f"[ERROR] {gif.id}: {e}")
            time.sleep(0.2)

        page += 1

    print(f"\nDownloaded {total_downloaded} GIFs from user '{username}' to {folder}")


def main():
    parser = argparse.ArgumentParser(
        description="Download GIFs/videos from redgifs.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--url", "-u", nargs="+", metavar="URL",
        help="One or more redgifs.com/watch/... URLs (or bare IDs)",
    )
    mode.add_argument(
        "--search", "-s", metavar="QUERY",
        help="Search query to download GIFs for",
    )
    mode.add_argument(
        "--user", metavar="USERNAME",
        help="Download all GIFs from a user's profile",
    )

    parser.add_argument(
        "--folder", "-f", metavar="DIR", default=str(Path.home() / "Downloads" / "redgifs"),
        help="Output directory (default: ~/Downloads/redgifs)",
    )
    parser.add_argument(
        "--quality", "-q", choices=["hd", "sd"], default="hd",
        help="Video quality (default: hd)",
    )
    parser.add_argument(
        "--count", "-c", type=int, default=40,
        help="Number of results per page for --search (default: 40)",
    )
    parser.add_argument(
        "--pages", "-p", type=int, default=1,
        help="Number of pages to fetch for --search (default: 1)",
    )
    parser.add_argument(
        "--max", "-m", type=int, default=0,
        help="Max number of GIFs to download for --user (0 = all)",
    )

    args = parser.parse_args()
    folder = Path(args.folder).expanduser().resolve()

    print("Logging in to RedGIFs API...")
    api = redgifs.API()
    api.login()

    try:
        if args.url:
            download_by_urls(api, args.url, folder, args.quality)
        elif args.search:
            download_by_search(api, args.search, folder, args.quality, args.count, args.pages)
        elif args.user:
            download_by_user(api, args.user, folder, args.quality, args.max)
    finally:
        api.close()


if __name__ == "__main__":
    main()
