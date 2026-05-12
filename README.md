# KTCK The Ticket – Stream Recorder

Continuously records the live audio stream from [The Ticket (KTCK)](https://theticket.com) using `ffmpeg`, automatically segmenting output files at show boundaries. Skips syndicated filler (Fox Sports Radio, Ticket Top 10) by default, and supports custom override events for playoffs, special coverage, and overnights.

## How it works

```
Stream (Triton/StreamTheWorld AAC)
         │
         ▼
    recorder.py
         │
         ├─ Checks overrides.json every ~60 s
         ├─ Segments at show boundaries (Central Time)
         ├─ Skips Fox Sports Radio / Ticket Top 10 unless overridden
         └─ Writes  YYYY-MM-DD_HHMM_Show_Name.aac  →  output dir
```

`overrides.json` is re-read from disk on every check — edit it at any time without restarting the process.

---

## Prerequisites

| Dependency | Version | Install |
|---|---|---|
| Python | 3.9+ | `brew install python` |
| ffmpeg | any recent | `brew install ffmpeg` |
| Docker (optional) | 20+ | [docs.docker.com](https://docs.docker.com/get-docker/) |

---

## Quick start

```bash
git clone <repo>
cd ticket-recorder

# Run manually (Ctrl-C to stop, saves partial segment)
python3 recorder.py

# Custom output directory
python3 recorder.py --output-dir ~/Dropbox/TheTicket

# Split each show into 30-minute files for easy browsing and selective upload
python3 recorder.py --segment-minutes 30

# Fixed 1-hour segments, ignore show schedule
python3 recorder.py --no-schedule
```

Output files are named `YYYY-MM-DD_HHMM_Show_Name.aac`, e.g.:

```
2026-05-12_0530_The_Musers_Pregame.aac
2026-05-12_0600_The_Musers.aac
2026-05-12_0900_The_Invasion.aac
2026-05-12_1300_The_Sweet_Spot.aac
2026-05-12_1500_The_Hardline.aac
```

---

## Usage example

Starting the recorder mid-morning on a weekday. It joins The Invasion already in progress, records through the afternoon, then sleeps through the evening Fox Sports Radio block until a playoff override kicks in:

```
$ python3 recorder.py --output-dir ~/Dropbox/TheTicket

2026-05-12 10:34:01  INFO     Output directory : /Users/marcus/Dropbox/TheTicket
2026-05-12 10:34:01  INFO     Stream URL       : https://playerservices.streamtheworld.com/...
2026-05-12 10:34:01  INFO     Schedule mode    : on
2026-05-12 10:34:01  INFO     ▶  'The Invasion'  →  2026-05-12_1034_The_Invasion.aac  (~146 min, ends ~13:00)
2026-05-12 13:00:02  INFO     ✔  Segment complete: 2026-05-12_1034_The_Invasion.aac
2026-05-12 13:00:03  INFO     ▶  'The Sweet Spot'  →  2026-05-12_1300_The_Sweet_Spot.aac  (~120 min, ends ~15:00)
2026-05-12 15:00:02  INFO     ✔  Segment complete: 2026-05-12_1300_The_Sweet_Spot.aac
2026-05-12 15:00:03  INFO     ▶  'The Hardline'  →  2026-05-12_1500_The_Hardline.aac  (~240 min, ends ~19:00)
2026-05-12 19:00:04  INFO     ✔  Segment complete: 2026-05-12_1500_The_Hardline.aac
# Fox Sports Radio slot — recorder sleeps, checks overrides.json every 60 s
2026-05-12 21:07:00  INFO     Override detected — waking from skipped slot early
2026-05-12 21:07:00  INFO     ★  Override 'Stars Playoff Game'  →  2026-05-12_2107_Stars_Playoff_Game.aac  (~113 min, ends ~23:00)
2026-05-12 23:00:01  INFO     ✔  Segment complete: 2026-05-12_2107_Stars_Playoff_Game.aac
# Back to sleeping through Fox Sports Radio until 5:30 AM
```

Resulting files in `~/Dropbox/TheTicket/`:

```
2026-05-12_1034_The_Invasion.aac        146 min
2026-05-12_1300_The_Sweet_Spot.aac      120 min
2026-05-12_1500_The_Hardline.aac        240 min
2026-05-12_2107_Stars_Playoff_Game.aac  113 min  ← override
```

To stop gracefully at any time (`Ctrl-C` or `SIGTERM`), the current segment is saved before exit:

```
^C
2026-05-12 16:22:10  INFO     Shutdown signal received — finishing current segment and stopping…
2026-05-12 16:22:10  INFO     Partial segment saved: 2026-05-12_1500_The_Hardline.aac
```

---

## Default show schedule

Sourced from [theticket.com/full-programming-schedule](https://www.theticket.com/full-programming-schedule). All times are **Central Time**.

### Weekdays (Mon–Fri)

| Time | Show | Recorded |
|---|---|---|
| 12:00 – 3:30 AM | Fox Sports Radio | No |
| 3:30 – 5:30 AM | The Ticket Top 10 | No |
| 5:30 – 6:00 AM | The Musers Pregame | Yes |
| 6:00 – 9:00 AM | The Musers | Yes |
| 9:00 AM – 1:00 PM | The Invasion | Yes |
| 1:00 – 3:00 PM | The Sweet Spot | Yes |
| 3:00 – 7:00 PM | The Hardline | Yes |
| 7:00 – 9:00 PM | The Ticket Top 10 | No |
| 9:00 PM – 12:00 AM | Fox Sports Radio | No |

### Saturday

| Time | Show | Recorded |
|---|---|---|
| 12:00 – 8:00 AM | Fox Sports Radio | No |
| 8:00 – 10:00 AM | The Teebox | Yes |
| 10:00 AM – 12:00 PM | College Football Tailgate | Yes |
| 12:00 – 2:00 PM | Afternoon Delight | Yes |
| 2:00 – 4:00 PM | The Kick Around | Yes |
| 4:00 PM – 12:00 AM | Fox Sports Radio | No |

### Sunday

| Time | Show | Recorded |
|---|---|---|
| 12:00 – 7:00 AM | Fox Sports Radio | No |
| 7:00 – 9:00 AM | Mark Elfenbein | Yes |
| 9:00 AM – 12:00 PM | Work in Progress | Yes |
| 12:00 PM – 12:00 AM | Fox Sports Radio | No |

To change what gets recorded, edit `SHOW_SCHEDULE` in `recorder.py` and set `"record": true` or `"record": false` on any entry.

---

## Custom overrides

`overrides.json` lets you schedule recordings that fall outside the normal pattern — playoff coverage, all-day Saturday events, or overnights — without touching the code. The file is re-read from disk on every 60-second check, so changes take effect immediately even while the recorder is running.

### Format

```json
[
  {
    "name":  "Display name used in the output filename",
    "start": "YYYY-MM-DD HH:MM",
    "end":   "YYYY-MM-DD HH:MM"
  }
]
```

All times are **Central Time**. `_comment` keys are ignored.

### Common patterns

**Evening game (normally skipped slot)**
```json
{ "name": "Cowboys Preseason Game", "start": "2026-08-10 18:30", "end": "2026-08-10 23:00" }
```

**All-day Saturday** (covers Fox Sports Radio blocks too)
```json
{ "name": "All Day Saturday Coverage", "start": "2026-07-04 00:00", "end": "2026-07-05 00:00" }
```

**Overnight spanning midnight** (set `end` to the next calendar day)
```json
{ "name": "New Year Eve Special", "start": "2026-12-31 21:00", "end": "2027-01-01 03:00" }
```

Overrides take priority over the default schedule. When an override is active during a normally-skipped slot, the recorder wakes within ~60 seconds and starts recording.

---

## Segmenting recordings

By default the recorder writes one file per show (e.g. a single 4-hour Hardline file). Use `--segment-minutes` to split into shorter chunks, making it easy to browse, trim, and selectively upload individual segments.

```bash
# 30-minute chunks — The Hardline (3–7 PM) becomes 8 files
python3 recorder.py --segment-minutes 30
```

Resulting files:

```
2026-05-12_1500_The_Hardline.aac   30 min
2026-05-12_1530_The_Hardline.aac   30 min
2026-05-12_1600_The_Hardline.aac   30 min
2026-05-12_1630_The_Hardline.aac   30 min
2026-05-12_1700_The_Hardline.aac   30 min
2026-05-12_1730_The_Hardline.aac   30 min
2026-05-12_1800_The_Hardline.aac   30 min
2026-05-12_1830_The_Hardline.aac   30 min
```

Segmentation also applies to override recordings — a 3-hour Cowboys playoff game with `--segment-minutes 30` produces six 30-minute files you can upload individually to Transistor or any podcast host.

---

## Retention policy

`cleanup.py` deletes `.aac` recordings older than a configurable number of days. Run it manually or schedule it via `deploy.sh`.

### Usage

```bash
# Preview what would be deleted (no files touched)
python3 cleanup.py --dry-run

# Delete recordings older than 30 days, keep override/special events forever
python3 cleanup.py --days 30 --keep-special

# Custom directory
python3 cleanup.py --output-dir ~/Dropbox/TheTicket --days 30 --keep-special
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--days N` | 30 | Delete files with a modification time older than N days |
| `--keep-special` | off | Preserve any recording whose show name isn't in the regular schedule (i.e. override events like `Stars_Playoff_Game`) regardless of age |
| `--dry-run` | off | Print what would be deleted without touching any files |
| `--output-dir DIR` | `~/ticket_recordings` | Directory to scan (recursive) |

### How special-event detection works

The recorder names files after the show: `The_Hardline`, `The_Musers`, etc. Any file whose embedded show name is **not** in the known schedule (e.g. `Stars_Playoff_Game`, `Cowboys_Preseason`) is treated as a special event and preserved. No separate tracking file is needed.

### Sample output

```
2026-05-12 02:00:01  INFO  Directory  : /Dropbox/TheTicket
2026-05-12 02:00:01  INFO  Retain     : 30 days  (cutoff: 2026-04-12 07:00 UTC)
2026-05-12 02:00:01  INFO  Keep special: yes — override recordings will never be deleted
  Deleted       2026-04-01_0600_The_Musers.aac            41 days  87.3 MB
  Deleted       2026-04-01_1500_The_Hardline.aac          41 days  174.2 MB
  KEPT(special) 2026-04-01_1900_Stars_Playoff_Game.aac    41 days  130.1 MB

2026-05-12 02:00:02  INFO  Deleted      :   2 file(s)   261.5 MB freed
2026-05-12 02:00:02  INFO  Special kept :   1 file(s)   130.1 MB  (override recordings)
2026-05-12 02:00:02  INFO  Retained     :  47 file(s)     3.8 GB
```

### Scheduling

Install a daily 2 AM cleanup job with one command:

```bash
# Schedule (macOS → launchd, Linux → cron)
./deploy.sh schedule-cleanup --output-dir ~/Dropbox/TheTicket --days 30

# Remove
./deploy.sh unschedule-cleanup
```

At 270 GB remaining on a 3 TB Dropbox plan at ~88 GB/year of recordings, 30-day retention keeps the steady-state footprint at ~7 GB — the recorder will never be what fills up your Dropbox.

---

## Deployment

### Option A — Docker (recommended for cloud/VPS)

```bash
# Start in the background (auto-restarts on crash or server reboot)
docker compose up -d

# View live logs
docker compose logs -f

# Stop
docker compose down
```

Recordings are written to `./recordings/` on the host. Edit `overrides.json` on the host at any time — the container picks it up without a rebuild.

To change the recordings path, edit `docker-compose.yml`:

```yaml
volumes:
  - /mnt/storage/TheTicket:/recordings
```

### Option B — Native macOS (launchd)

`deploy.sh` installs a launchd user agent that starts at login and auto-restarts on crash.

> **Important:** launchd cannot access files under `~/Library/CloudStorage/` (Dropbox sync folder)
> without Full Disk Access. Copy the project to your home directory first:
>
> ```bash
> cp -r "/path/to/ticket-recorder" ~/ticket-recorder
> cd ~/ticket-recorder
> ```
>
> Run all `deploy.sh` commands from `~/ticket-recorder/`, not from the Dropbox copy.
> Recordings can still go to `~/Dropbox/TheTicket/` — only the *scripts* need to be outside CloudStorage.

```bash
cd ~/ticket-recorder

# Install (starts at login, auto-restarts on crash)
./deploy.sh install --output-dir ~/Dropbox/TheTicket

# Verify it started — PID column should be non-zero
launchctl list | grep theticket

# Watch live log
tail -f ~/ticket-recorder/logs/recorder.log

# Schedule daily 2 AM cleanup
./deploy.sh schedule-cleanup --output-dir ~/Dropbox/TheTicket --days 30

# Schedule 15-minute health monitor with macOS notifications
./deploy.sh schedule-monitor --output-dir ~/Dropbox/TheTicket

# Stop / remove everything
./deploy.sh uninstall
./deploy.sh unschedule-cleanup
./deploy.sh unschedule-monitor
```

Service files and logs are managed automatically:

| Item | Path |
|---|---|
| Recorder plist | `~/Library/LaunchAgents/com.theticket.recorder.plist` |
| Cleanup plist | `~/Library/LaunchAgents/com.theticket.cleanup.plist` |
| Monitor plist | `~/Library/LaunchAgents/com.theticket.monitor.plist` |
| Recorder log | `~/ticket-recorder/logs/recorder.log` |
| Cleanup log | `~/ticket-recorder/logs/cleanup.log` |
| Monitor log | `~/ticket-recorder/logs/monitor.log` |

The plist includes `EnvironmentVariables/PATH` so Homebrew-installed `ffmpeg` is found automatically.

### Option C — Native Linux (systemd)

Same `deploy.sh` script — it detects the OS and installs a systemd user unit instead.

```bash
./deploy.sh install
./deploy.sh install --output-dir /mnt/storage/TheTicket

# Schedule daily 2 AM cleanup via crontab
./deploy.sh schedule-cleanup --output-dir /mnt/storage/TheTicket --days 30

# Schedule 15-minute health monitor
./deploy.sh schedule-monitor --output-dir /mnt/storage/TheTicket
./deploy.sh unschedule-cleanup
./deploy.sh unschedule-monitor
```

| Item | Path |
|---|---|
| Unit file | `~/.config/systemd/user/com.theticket.recorder.service` |
| Logs | `journalctl --user -u com.theticket.recorder -f` |
| Cleanup | crontab entry — `crontab -l \| grep cleanup` |
| Monitor | crontab entry — `crontab -l \| grep monitor` |

The script calls `loginctl enable-linger` automatically so the service persists after SSH logout. If that requires root, run it manually:

```bash
sudo loginctl enable-linger $USER
```

---

## System health monitoring

`monitor_health.py` runs every 15 minutes as a background launchd/cron job and sends macOS notifications if anything needs attention.

### What it checks

| Check | Alert condition | Notification |
|---|---|---|
| Recorder service | PID is zero or exit code non-zero | urgent (Basso sound) |
| File freshness | Newest `.aac` not updated in 20 min *during a recording window* | urgent |
| Disk space | Free space below 50 GB | info (Ping sound) |

The freshness check is **schedule-aware** — it stays silent during Fox Sports Radio and other skipped slots (7 PM – 5:30 AM weekdays, etc.) so you don't get false alerts overnight.

### Install / remove

```bash
cd ~/ticket-recorder

# Install (runs every 15 minutes)
./deploy.sh schedule-monitor --output-dir ~/Dropbox/TheTicket

# Run manually at any time
python3 monitor_health.py --output-dir ~/Dropbox/TheTicket

# Test that macOS notifications are working
python3 monitor_health.py --test

# Remove
./deploy.sh unschedule-monitor
```

### Sample healthy output

```
2026-05-12 15:35:54  Health check — Tue 03:35 PM CDT
  ✔  Service running
  ✔  File freshness: 2026-05-12_1500_The_Hardline.aac updated 1 min ago
  ✔  Disk space: 615.1 GB free of 3722 GB (83% used)
  All checks passed.
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--output-dir DIR` | `~/Dropbox/TheTicket` | Directory to check for recordings |
| `--stale-mins N` | 20 | Minutes of no file growth before alerting |
| `--disk-warn-gb N` | 50 | GB free threshold for disk alert |
| `--test` | off | Fire all three alerts immediately (notification test) |

---

## Monitoring show transitions

`monitor_transition.py` watches the output directory and fires a banner the moment a new show file appears, with file sizes reported every 5 minutes. Useful when verifying a new deployment or checking a specific boundary.

```bash
# Watch indefinitely (Ctrl-C to stop)
python3 monitor_transition.py

# Custom output directory
python3 monitor_transition.py ~/Documents/TheTicket

# Auto-stop at a specific time (e.g. 5 minutes after a transition)
python3 monitor_transition.py --stop-after 19:05   # watch 7 PM Hardline → sleep
```

Sample output at a rollover:

```
[02:59:54 PM CDT]
  2026-05-12_1300_The_Sweet_Spot.aac     42.22 MB  modified 14:59:01

==============================================================
  ✔  ROLLOVER at 03:00:02 PM CDT
  New file(s): 2026-05-12_1500_The_Hardline.aac
==============================================================

[03:00:24 PM CDT]
  2026-05-12_1300_The_Sweet_Spot.aac     42.22 MB  modified 14:59:01
  2026-05-12_1500_The_Hardline.aac        256 KB   modified 15:00:08
  → 2026-05-12 15:00:02  ▶  'The Hardline'  →  2026-05-12_1500_The_Hardline.aac  (~239 min)
```

---

## Troubleshooting

**Service exits immediately on macOS (exit code 1 or 2)**
Two known causes:

1. *Scripts in `~/Library/CloudStorage/`* — launchd can't read files there without Full Disk Access.
   Fix: copy the project to `~/ticket-recorder/` and reinstall from there.

2. *`ffmpeg` not found* — launchd doesn't inherit your shell PATH, so Homebrew binaries are invisible.
   Fix: already handled — `deploy.sh` embeds `/opt/homebrew/bin` in the plist's `EnvironmentVariables`.
   If you installed ffmpeg elsewhere, update the PATH line in the generated plist.

**Fragment files (`ShowName_2.aac`, `ShowName_3.aac`, …)**
Appear when the service crash-loops near a show boundary (each restart tries to open the same
minute-timestamp file, triggering the collision counter). Once the service is stable they stop.
Remove fragments manually:

```bash
# Preview
ls ~/Dropbox/TheTicket/*_[0-9].aac ~/Dropbox/TheTicket/*_[0-9][0-9].aac 2>/dev/null

# Delete
rm ~/Dropbox/TheTicket/*_[0-9].aac ~/Dropbox/TheTicket/*_[0-9][0-9].aac 2>/dev/null
```

**Check service health at a glance**

```bash
launchctl list | grep theticket   # PID non-zero = running; exit code 0 = healthy
tail -5 ~/ticket-recorder/logs/recorder.error.log   # last error if any
```

---

## Stream details

| Property | Value |
|---|---|
| Station | KTCK-AM/FM — SportsRadio 96.7 & 1310 The Ticket, Dallas |
| Provider | Triton Digital / StreamTheWorld |
| Stream URL | `https://playerservices.streamtheworld.com/api/livestream-redirect/KTCKAMAAC.aac` |
| Format | AAC-LC, 48 kbps |
| Output format | `.aac` (stream-copied, no re-encoding) |

The redirect URL is stable and followed automatically by `ffmpeg`. If the stream drops, `ffmpeg` reconnects with `-reconnect`/`-reconnect_streamed` flags. If it exits non-zero, the recorder waits 10 seconds and retries.

---

## Running the tests

```bash
pip3 install pytest
pytest test_override_sleep.py -v
```

The test suite (14 tests) covers:

- `sleep_until` — breaks on override detection, polls every tick, respects shutdown, caps sleep at 60 s
- `get_active_override` — file absent/empty/future/expired/active/malformed, live file reload
- Integration — override wakes recorder mid-sleep; skipped slot with no override never calls ffmpeg

---

## Project structure

```
ticket-recorder/
├── recorder.py            # Live stream recorder — segmented by show schedule
├── cleanup.py             # Retention policy — delete old recordings, keep special events
├── monitor_health.py      # 15-min health check — service, file freshness, disk space alerts
├── monitor_transition.py  # Show rollover monitor — watch for new files, print banners
├── overrides.json         # Custom recording events (edit anytime, no restart needed)
├── deploy.sh              # macOS (launchd) and Linux (systemd/cron) service installer
├── verify_recording.py    # End-to-end pipeline verification (stream, segment, SIGTERM)
├── Dockerfile             # Container image definition
├── docker-compose.yml     # Compose service configuration
├── test_override_sleep.py # Pytest test suite (14 tests)
├── test_full_pipeline.py  # Integration test — recording + cleanup (18 checks)
├── logs/                  # Created by deploy.sh — recorder.log, cleanup.log, monitor.log
└── recordings/            # Default output dir for Docker Compose
```

### macOS deployment note

For production use, run the service from a copy **outside** Dropbox:

```
~/ticket-recorder/     ← service runs from here (launchd-accessible)
~/Dropbox/TheTicket/   ← recordings land here (Dropbox syncs to cloud)
```

Keep the Dropbox copy (`Warp Projects/ticket-recorder/`) as the development repo and git remote.
