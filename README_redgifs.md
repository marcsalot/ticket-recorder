# download_redgifs.py

Download GIFs/videos from [redgifs.com](https://redgifs.com) by URL, search query, or user profile.

## Requirements

- Python 3.8+
- The [`redgifs`](https://github.com/scrazzz/redgifs) library

## Installation

```bash
pip3 install -r requirements-redgifs.txt
```

## Usage

### Download specific URLs

Pass one or more `redgifs.com/watch/...` URLs, or bare GIF IDs:

```bash
python3 download_redgifs.py --url https://redgifs.com/watch/someGifId
python3 download_redgifs.py --url https://redgifs.com/watch/abc https://redgifs.com/watch/xyz
python3 download_redgifs.py --url someGifId anotherGifId
```

### Download by search query

Fetches trending results for the query. Use `--pages` to pull multiple pages.

```bash
python3 download_redgifs.py --search "funny cats"
python3 download_redgifs.py --search "funny cats" --count 40 --pages 3
```

### Download a user's GIFs

Downloads all GIFs from a creator's profile, newest first. Use `--max` to cap the total.

```bash
python3 download_redgifs.py --user someusername
python3 download_redgifs.py --user someusername --max 50
```

## Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--url URL [URL ...]` | `-u` | — | One or more URLs or bare GIF IDs |
| `--search QUERY` | `-s` | — | Search query |
| `--user USERNAME` | | — | Creator username |
| `--folder DIR` | `-f` | `./redgifs_downloads` | Output directory |
| `--quality {hd,sd}` | `-q` | `hd` | Video quality (falls back to SD if HD unavailable) |
| `--count N` | `-c` | `40` | Results per page (for `--search`) |
| `--pages N` | `-p` | `1` | Pages to fetch (for `--search`) |
| `--max N` | `-m` | `0` (all) | Max GIFs to download (for `--user`) |

`--url`, `--search`, and `--user` are mutually exclusive — pick one per run.

## Output

Files are saved as `<gif_id>.mp4` in the output folder. Already-downloaded files are skipped automatically, so re-running the same command is safe.

## Notes

- No API key or account required — the script uses a temporary anonymous token.
- A short delay is added between downloads to be polite to the API.
- RedGIFs serves video files (`.mp4`), not animated GIFs, even though the site calls them GIFs.
