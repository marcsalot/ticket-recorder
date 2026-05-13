# YouTube Audio Recorder

Downloads or records YouTube audio as MP3 using [yt-dlp](https://github.com/yt-dlp/yt-dlp). Works with any public YouTube video or live stream — no video is ever downloaded or stored.

---

## Requirements

| Dependency | Install |
|---|---|
| Python 3.9+ | `brew install python` |
| yt-dlp | `brew install yt-dlp` |
| ffmpeg | `brew install ffmpeg` |

---

## Quick start

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=...
```

Output goes to `~/Downloads/YouTube Audio/` by default.

---

## Usage

### Download a video

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=...
```

### Place output in a named subfolder

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --folder "Show Themes"
```

Output: `~/Downloads/YouTube Audio/Show Themes/YYYY-MM-DD_HHMM_Title.mp3`

### Group by channel name automatically

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --by-channel
```

Output: `~/Downloads/YouTube Audio/<Channel Name>/YYYY-MM-DD_HHMM_Title.mp3`

### Record a live stream

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --live
```

Stays connected and records until the stream ends. Starts from the beginning of the DVR window.

### Custom output directory

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --output-dir ~/Music
```

### Dry-run (preview without downloading)

```bash
python3 youtube_recorder.py https://youtube.com/watch?v=... --dry-run
```

Fetches metadata and logs the planned output path, but downloads nothing.

---

## Options

| Flag | Default | Description |
|---|---|---|
| `--output-dir DIR` | `~/Downloads/YouTube Audio` | Root directory for saved files |
| `--folder NAME` | — | Place output in a named subfolder |
| `--by-channel` | off | Auto-create a subfolder from the channel name |
| `--live` | off | Live stream mode — record until the stream ends |
| `--dry-run` | off | Preview only, no download |

`--folder` takes precedence over `--by-channel` when both are provided.

---

## Output file naming

Files are named using the video's original upload date and title:

```
YYYY-MM-DD_HHMM_Video_Title.mp3
```

For example:
```
2023-09-03_0000_Bionic_Six_-_Theme__Opening.mp3
```

If the upload date is unavailable, the current date and time are used.

---

## Notifications

On macOS, a system notification is sent when a download completes or fails.

---

## Running the tests

```bash
pip install pytest
pytest test_youtube_recorder.py -v
```

The test suite (25 tests) covers filename sanitisation, metadata fetching, dependency checking, dry-run behaviour, output path resolution, date stamp handling, live-stream flags, and download success/failure scenarios. All tests are fully mocked — no network access required.
