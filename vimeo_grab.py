#!/usr/bin/env python3
"""
vimeo_grab.py — uses a real headless browser to capture the Vimeo player config,
then downloads the best quality stream with ffmpeg.

Usage:
    python3 vimeo_grab.py [vimeo_url] [output_dir]

Defaults:
    vimeo_url  = https://vimeo.com/134371535
    output_dir = ~/Downloads
"""

import sys
import json
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

VIMEO_URL = sys.argv[1] if len(sys.argv) > 1 else "https://vimeo.com/134371535"
OUTPUT_DIR = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else Path.home() / "Downloads"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

player_config = {}

def on_response(response):
    global player_config
    url = response.url
    if "player.vimeo.com/video/" in url and "/config" in url:
        try:
            player_config = response.json()
            print(f"  [config captured] {url[:100]}")
        except Exception:
            pass

print(f"Opening {VIMEO_URL} in Chromium (non-headless to pass Cloudflare)...")
print("A browser window will open — leave it alone for ~30 seconds.")

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    page = context.new_page()
    page.on("response", on_response)

    page.goto(VIMEO_URL, wait_until="load", timeout=45000)
    print("Page loaded — waiting for player config (15s)...")
    time.sleep(15)

    # Try to click play to trigger config fetch if not yet captured
    if not player_config:
        try:
            page.locator("[aria-label*='play' i], button.play").first.click(timeout=3000)
            print("Clicked play — waiting 8s for config...")
            time.sleep(8)
        except Exception:
            pass

    browser.close()

if not player_config:
    print("\nFailed to capture player config. The page may have been blocked.")
    sys.exit(1)

# Extract stream URLs from config
# Vimeo config has: request.files.hls.cdns (HLS) and request.files.progressive (MP4)
files = player_config.get("request", {}).get("files", {})

# Try progressive MP4 first (easiest to download, no stitching needed)
progressive = files.get("progressive", [])
if progressive:
    # Sort by quality descending
    progressive.sort(key=lambda x: x.get("width", 0), reverse=True)
    best = progressive[0]
    url = best.get("url")
    quality = f"{best.get('width', '?')}x{best.get('height', '?')} {best.get('quality', '')}"
    print(f"\nFound progressive MP4: {quality}")
    print(f"URL: {url[:100]}...")
    out_file = OUTPUT_DIR / "richard-norton-flow-complex-5.mp4"
    print(f"Downloading to {out_file}...")
    result = subprocess.run(["curl", "-L", "-o", str(out_file), url])
    if result.returncode == 0:
        print("Done!")
    else:
        print(f"curl failed. URL:\n{url}")
    sys.exit(result.returncode)

# Fall back to HLS
hls = files.get("hls", {})
cdns = hls.get("cdns", {})
default_cdn = hls.get("default_cdn", next(iter(cdns), None))
if default_cdn and default_cdn in cdns:
    master_url = cdns[default_cdn].get("url") or cdns[default_cdn].get("avc_url")
    if master_url:
        print(f"\nFound HLS master playlist ({default_cdn}):")
        print(f"URL: {master_url[:100]}...")
        out_file = OUTPUT_DIR / "richard-norton-flow-complex-5.mp4"
        print(f"Downloading with ffmpeg to {out_file}...")
        result = subprocess.run([
            "ffmpeg", "-i", master_url,
            "-c", "copy", "-y", str(out_file)
        ])
        if result.returncode == 0:
            print("Done!")
        else:
            print(f"ffmpeg failed. HLS URL:\n{master_url}")
        sys.exit(result.returncode)

print("\nCould not find a downloadable stream in the player config.")
print("Config files section:")
print(json.dumps(files, indent=2)[:1000])
sys.exit(1)
