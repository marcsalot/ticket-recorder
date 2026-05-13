# YouTube Audio Recorder

Downloads or records YouTube audio as MP3 using [yt-dlp](https://github.com/yt-dlp/yt-dlp). Works with any public YouTube video or live stream — no video is ever downloaded or stored.

---

## How it works

```
YouTube URL
     │
     ▼
youtube_recorder.py
     │
     ├─ Fetches metadata (title, channel, upload date) via yt-dlp --dump-json
     ├─ Resolves output path  (flat / --folder / --by-channel)
     ├─ Invokes yt-dlp -x to extract audio only  (no video downloaded)
     ├─ ffmpeg re-encodes to MP3 at best VBR quality
     └─ Writes  YYYY-MM-DD_HHMM_Title.mp3  →  output dir
```

The original upload date is embedded in the filename so recordings stay sorted chronologically regardless of when you download them.

---

## Prerequisites

| Dependency | Version | Install |
|---|---|---|
| Python | 3.9+ | `brew install python` |
| yt-dlp | any recent | `brew install yt-dlp` |
| ffmpeg | any recent | `brew install ffmpeg` |

Verify everything is in place:

```bash
yt-dlp --version
ffmpeg -version
python3 --version
```

The script will also check for missing tools on startup and print a clear error if anything is absent.

---

## Quick start

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=...
```

Output goes to `~/Downloads/YouTube Audio/` by default.

Always do a dry-run first to verify the planned output path before committing to a download:

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --dry-run
```

```
2026-05-12 22:16:00  INFO      Video title  : Bionic Six - Theme / Opening
2026-05-12 22:16:00  INFO      Channel      : Weliketvthemes
2026-05-12 22:16:00  INFO      Output file  : /Users/marcus/Downloads/YouTube Audio/2023-09-03_0000_Bionic_Six_-_Theme__Opening.mp3
2026-05-12 22:16:00  INFO      DRY RUN — no file will be downloaded.
```

---

## Usage examples

### Download a single video

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=...
```

### Place output in a named subfolder

Useful for organising by category (e.g. show themes, intros, sound effects).

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --folder "Show Themes"
```

Output: `~/Downloads/YouTube Audio/Show Themes/2023-09-03_0000_Title.mp3`

### Group by channel name automatically

Each download is filed under its YouTube channel name — handy when pulling multiple videos from different sources.

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --by-channel
```

Output: `~/Downloads/YouTube Audio/Weliketvthemes/2023-09-03_0000_Title.mp3`

### Record a live stream

Stays connected and records until the stream ends. Starts from the beginning of the DVR window if one is available.

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --live
```

Interrupt with `Ctrl-C` at any time — the partial file is preserved.

### Custom output directory

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --output-dir ~/Music
```

All subfolder options (`--folder`, `--by-channel`) work relative to whatever directory you specify here.

### Combine flags

```bash
# Save to a specific folder inside a custom output directory
python3 youtube_recorder.py https://youtube.com/watch?v=... \
    --output-dir ~/Dropbox/Audio \
    --folder "Intros"

# Group by channel, custom root
python3 youtube_recorder.py https://youtube.com/watch?v=... \
    --output-dir ~/Dropbox/Audio \
    --by-channel
```

---

## Options

| Flag | Default | Description |
|---|---|---|
| `--output-dir DIR` | `~/Downloads/YouTube Audio` | Root directory for all saved files |
| `--folder NAME` | — | Place output in this named subfolder |
| `--by-channel` | off | Auto-create a subfolder named after the YouTube channel |
| `--live` | off | Live stream mode — record until the stream ends |
| `--dry-run` | off | Fetch metadata and log the plan; download nothing |

**Precedence:** `--folder` takes priority over `--by-channel` when both are supplied.

---

## Output file naming

Files are named using the video's original upload date and sanitised title:

```
YYYY-MM-DD_HHMM_Video_Title.mp3
```

Examples:

```
~/Downloads/YouTube Audio/
    2023-09-03_0000_Bionic_Six_-_Theme__Opening.mp3

~/Downloads/YouTube Audio/Show Themes/
    2024-01-15_0000_The_Hardline_Theme.mp3
    2024-02-20_0000_The_Musers_Intro.mp3

~/Downloads/YouTube Audio/Weliketvthemes/
    2023-09-03_0000_Bionic_Six_-_Theme__Opening.mp3
    2023-10-11_0000_Airwolf_Theme.mp3
```

Special characters are stripped from the title; spaces become underscores. If the upload date is unavailable from metadata, the current date and time are used instead.

---

## Audio quality

yt-dlp selects the best available audio stream and ffmpeg re-encodes it to MP3 at VBR quality 0 (highest). Typical results:

| Source | Bitrate | File size (60 min) |
|---|---|---|
| YouTube (opus/webm) | ~260 kbps | ~115 MB |
| YouTube (AAC) | ~128 kbps | ~57 MB |

The output is always MP3 regardless of the source format, ensuring broad compatibility.

---

## Notifications

On macOS, a notification is sent via `osascript` when a download completes or fails. No setup required — it uses the system notification centre automatically.

---

## Running the tests

```bash
pip3 install pytest
pytest test_youtube_recorder.py -v
```

Expected output:

```
collected 25 items

test_youtube_recorder.py::TestSafeFilename::test_spaces_become_underscores       PASSED
test_youtube_recorder.py::TestSafeFilename::test_strips_special_characters       PASSED
...
25 passed in 0.10s
```

The suite is fully mocked — no network access or real downloads occur. Coverage includes:

- `_safe_filename` — character stripping, hyphen preservation, whitespace trimming, unicode
- `_fetch_metadata` — JSON parsing on success, `SystemExit` on failure
- `_check_deps` — exits when `yt-dlp` or `ffmpeg` missing; passes when both present
- `download_audio` (dry-run) — returns `None`, no subprocess called, no directory created
- Output path resolution — flat, `--folder`, `--by-channel`, precedence rules
- Date stamps — uses upload date from metadata; falls back to current time
- Live-stream flags — `--live-from-start` / `--no-part` present or absent correctly
- Success/failure — returns path on success, glob fallback for renamed files, `None` on error

---

## Troubleshooting

**`ERROR: Missing required tool(s): yt-dlp`**
Install with `brew install yt-dlp`. If Homebrew isn't available, use `pip3 install yt-dlp`.

**`yt-dlp metadata fetch failed: ERROR: Sign in to confirm your age`**
The video has age restrictions. Pass cookies from a logged-in browser session:
```bash
yt-dlp --cookies-from-browser chrome ...
```
This requires running yt-dlp directly, not through the script.

**`yt-dlp metadata fetch failed: ERROR: Video unavailable`**
The video is private, deleted, or region-locked.

**Download finishes but no MP3 found**
yt-dlp occasionally writes a slightly different filename. The script searches the output directory with a glob pattern to find the actual file and reports its name in the log.

**Live stream recording stops unexpectedly**
yt-dlp will exit if the stream goes offline. Re-run the command to resume — yt-dlp will pick up from where the DVR window allows.

---

## Project structure

```
ticket-recorder/
├── youtube_recorder.py        # Main script
├── test_youtube_recorder.py   # Pytest test suite (25 tests)
└── README_youtube_recorder.md # This file
```
