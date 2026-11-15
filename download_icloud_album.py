#!/usr/bin/env python3
"""Download an iCloud shared album to a local directory."""

import requests
import os
import json
import re
import sys

TOKEN = "B2BGnmlKoGlzpfJ"
DEST = "/Users/marcus/Library/CloudStorage/Dropbox/CSSD/Photos & Videos/Kids Belt Tests/20260516"

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.icloud.com",
    "Referer": "https://www.icloud.com/",
}


def fetch_webstream(token):
    """Fetch album stream metadata, following partition redirects."""
    host = "p23-sharedstreams.icloud.com"
    for _ in range(3):
        url = f"https://{host}/{token}/sharedstreams/webstream"
        r = requests.post(url, json={"streamCtag": None}, headers=HEADERS, timeout=60)
        if r.status_code == 330:
            new_host = r.json().get("X-Apple-MMe-Host")
            if new_host:
                host = new_host
                continue
        break
    r.raise_for_status()
    return r.json(), host


def fetch_asset_urls(host, token, photo_guids):
    """Fetch signed download URLs for a batch of photo GUIDs."""
    url = f"https://{host}/{token}/sharedstreams/webasseturls"
    r = requests.post(url, json={"photoGuids": photo_guids}, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()


def build_download_url(item, locations):
    """Construct a download URL from item metadata and optional locations map."""
    # Direct URL field
    if "url" in item:
        return item["url"]

    # url_location is a string key referencing the locations dict
    url_location = item.get("url_location", {})
    if isinstance(url_location, str):
        url_location = locations.get(url_location, {})

    scheme = url_location.get("scheme", "https")
    # locations may use "host" (str) or "hosts" (list)
    host_raw = url_location.get("host") or url_location.get("hosts")
    if isinstance(host_raw, list):
        host = host_raw[0] if host_raw else ""
    else:
        host = host_raw or ""
    # Fallback: url_location string value itself is the host
    if not host and isinstance(item.get("url_location"), str):
        host = item["url_location"]

    # Path is stored in url_path (with signed query params already included)
    path = item.get("url_path", item.get("url_key", ""))
    if host and path:
        return f"{scheme}://{host}{path}"

    return None


def main():
    os.makedirs(DEST, exist_ok=True)

    print(f"Fetching album stream for token: {TOKEN}")
    stream_data, host = fetch_webstream(TOKEN)

    photos = stream_data.get("photos", [])
    print(f"Found {len(photos)} items")

    if not photos:
        print("Album is empty or not accessible.")
        sys.exit(1)

    # Fetch signed download URLs in batches of 25
    photo_guids = [p["photoGuid"] for p in photos]
    checksum_map = {}
    locations = {}

    BATCH = 25
    total_batches = (len(photo_guids) + BATCH - 1) // BATCH
    for i in range(0, len(photo_guids), BATCH):
        batch = photo_guids[i:i + BATCH]
        bn = i // BATCH + 1
        print(f"Fetching URLs batch {bn}/{total_batches}...")
        data = fetch_asset_urls(host, TOKEN, batch)
        checksum_map.update(data.get("items", {}))
        locations.update(data.get("locations", {}))

    # Debug: print first item structure to help diagnose URL issues
    if checksum_map:
        first_key = next(iter(checksum_map))
        print(f"\n[DEBUG] First item structure:\n{json.dumps(checksum_map[first_key], indent=2)}")
        if locations:
            print(f"[DEBUG] Locations:\n{json.dumps(locations, indent=2)}")
        print()

    print(f"Downloading to: {DEST}")
    print("=" * 60)

    success = skip = fail = 0

    for idx, photo in enumerate(photos, 1):
        guid = photo.get("photoGuid", "")
        derivatives = photo.get("derivatives", {})

        # Pick the largest derivative (highest quality)
        best = max(
            derivatives.values(),
            key=lambda d: int(d.get("fileSize", 0)),
            default=None,
        )
        if not best:
            print(f"[{idx:3d}/{len(photos)}] SKIP  no derivatives: {guid[:16]}")
            skip += 1
            continue

        checksum = best.get("checksum", "")
        item = checksum_map.get(checksum)
        if not item:
            print(f"[{idx:3d}/{len(photos)}] SKIP  no URL data: {guid[:16]}")
            skip += 1
            continue

        url = build_download_url(item, locations)
        if not url:
            print(f"[{idx:3d}/{len(photos)}] SKIP  can't build URL from: {json.dumps(item)[:100]}")
            skip += 1
            continue

        # Determine file extension from media type
        media_type = photo.get("mediaAssetType", "image")
        if "video" in media_type.lower():
            ext = ".mov"
        elif "gif" in media_type.lower():
            ext = ".gif"
        else:
            ext = ".jpg"

        # Build filename from caption if available
        caption = photo.get("caption", "").strip()
        if caption:
            safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", caption)[:80]
            filename = f"{idx:04d}_{safe}{ext}"
        else:
            filename = f"{idx:04d}_{guid[:16]}{ext}"

        dest_path = os.path.join(DEST, filename)

        if os.path.exists(dest_path):
            print(f"[{idx:3d}/{len(photos)}] EXISTS {filename}")
            skip += 1
            continue

        size_mb = int(best.get("fileSize", 0)) / 1024 / 1024
        print(f"[{idx:3d}/{len(photos)}] DL    {filename} ({size_mb:.1f} MB)...", end="", flush=True)

        try:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            actual_mb = os.path.getsize(dest_path) / 1024 / 1024
            print(f" OK ({actual_mb:.1f} MB)")
            success += 1
        except Exception as e:
            print(f" FAIL: {e}")
            if os.path.exists(dest_path):
                os.remove(dest_path)
            fail += 1

    print("=" * 60)
    print(f"Done: {success} downloaded, {skip} skipped, {fail} failed")


if __name__ == "__main__":
    main()
