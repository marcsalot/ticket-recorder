# KTCK The Ticket – Stream Recorder

Continuously records the live audio stream from [The Ticket (KTCK)](https://theticket.com) using `ffmpeg`, automatically segmenting output files at show boundaries. Skips syndicated filler (Fox Sports Radio, Ticket Top 10) by default, and supports custom override events for playoffs, special coverage, and overnights.

## System architecture

Two deployment modes are supported. Both write files to the same Dropbox folder.

### Mode A — Mac (local)
```
KTCK Stream ──► recorder.py (launchd) ──► ~/Dropbox/TheTicket/
                     │                           │
              monitor_health.py          Dropbox client syncs
              (every 15 min)             to cloud automatically
```

### Mode B — VPS (recommended for 24/7 reliability)
```
KTCK Stream ──► recorder.py (systemd) ──► ~/recordings/
                     │                           │
              rclone copy cron            rclone copy ──► dropbox:TheTicket
              (every 15 min)                                    │
                                               Dropbox client on Mac
                                               syncs to ~/Dropbox/TheTicket/
```

> ⚠ **rclone safety:** Always use `rclone copy` (not `rclone sync`) when pushing from the VPS.
> `rclone sync` makes the destination match the source — if the VPS recordings folder is empty
> (e.g. just after setup), it will delete everything in Dropbox. `rclone copy` only adds files.

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
| 5:30 – 6:00 AM | The Musers Pregame | No |
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
| 8:00 – 10:00 AM | The Teebox | No |
| 10:00 AM – 12:00 PM | College Football Tailgate | No |
| 12:00 – 2:00 PM | Afternoon Delight | No |
| 2:00 – 4:00 PM | The Kick Around | No |
| 4:00 PM – 12:00 AM | Fox Sports Radio | No |

### Sunday

| Time | Show | Recorded |
|---|---|---|
| 12:00 – 7:00 AM | Fox Sports Radio | No |
| 7:00 – 9:00 AM | Mark Elfenbein | No |
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

## Commercial stripping

`strip_commercials.py` detects and removes commercial breaks from a completed recording.
It uses two independent signals:

1. **Fragmentation clustering** — commercial breaks appear as dense clusters of short
   silence-delimited sub-segments (~1–45s each). The algorithm groups adjacent short
   segments into candidate zones and applies a duration filter [60s, 420s].
2. **RMS gate** — on KTCK, show hosts are louder than most commercials. Zone average
   RMS is compared against a 75th-percentile baseline from long content segments.
   Set `--threshold 0` to rely on clustering alone (useful for overnight specials).

Output is a `_clean.aac` alongside the original — **the original is never modified**.

### Usage

```bash
# Always dry-run first to review the cut list
python3 strip_commercials.py recording.aac --dry-run

# For overnight/special events: use clustering only (no RMS gate)
python3 strip_commercials.py recording.aac --dry-run --threshold 0

# Strip for real once the dry-run looks correct
python3 strip_commercials.py recording.aac
python3 strip_commercials.py recording.aac --threshold 0
```

### Sample dry-run output

```
   Start       End     Dur     RMS  Type
──────────────────────────────────────────────
00:00:00  00:24:12   1452s  -23.5  content
00:24:12  00:30:07    355s  -25.1  COMMERCIAL ✂
00:30:08  00:45:33    925s  -23.1  content
00:45:34  00:50:08    274s  -45.6  COMMERCIAL ✂
00:50:09  00:56:11    362s  -21.3  content
──────────────────────────────────────────────
  Content    : 00:45:40  (83%)
  Commercial : 00:09:25  (17%)
```

### Tuning flags

| Flag | Default | Effect |
|---|---|---|
| `--threshold N` | 3.0 | dB below baseline RMS to classify as commercial; 0 = cluster-only |
| `--min-break N` | 60 | Minimum break duration in seconds |
| `--max-break N` | 420 | Maximum break duration in seconds |
| `--max-spot N` | 60 | Max sub-segment length to include in a cluster |
| `--max-intra-gap N` | 12 | Max silence gap inside a break |
| `--dry-run` | off | Analyse only, write nothing |

### Baseline results (2026-05-12)

| Show | Raw | Clean | Removed |
|---|---|---|---|
| The Invasion (~75 min) | 26 MB | 17 MB | 39% |
| The Sweet Spot (~100 min) | 42 MB | 29 MB | 34% |
| The Hardline (240 min) | 84 MB | 55 MB | 35% |

---

## Merging split recordings

If the recorder restarts mid-show (stream drop, crash, or machine wake from sleep),
the same show gets two files with different timestamps:

```
2026-05-13_0600_The_Musers.aac    ← 3 hours before failure
2026-05-13_0832_The_Musers.aac    ← resumed after restart
```

`merge_segments.py` detects these pairs automatically and joins them into one file
using ffmpeg concat — lossless, no re-encoding, no quality loss.

```bash
# Preview what would be merged (safe, touches nothing)
python3 merge_segments.py ~/Dropbox/TheTicket --dry-run

# Merge (replaces the earliest file with the joined version, deletes the later one)
python3 merge_segments.py ~/Dropbox/TheTicket
```

Sample output:

```
Found 1 show(s) with split segments:

  ✔  Merged 2 segments → 2026-05-13_0600_The_Musers.aac
     180.2 MB absorbed  →  412.8 MB total

Done: 1 merged  0 skipped
```

**Detection logic:** groups files by `date + show_name`. If two or more files share
the same date and show but have different start timestamps, they are merge candidates.
Same-minute collision-counter files (`_2.aac`, `_3.aac`) are treated as a single group
and merged correctly too.

> ⚠ **Do not run on recordings made with `--segment-minutes`** — those intentional
> splits share the same show name and would be incorrectly joined into one file.

### Integrate with the processor

Pass `--merge` to `process_recordings.sh` to merge splits before stripping commercials,
so the entire pipeline runs on a single clean file:

```bash
bash ~/ticket-recorder/process_recordings.sh --merge --output-dir ~/Dropbox/TheTicket
```

---

## Automated post-processing

`process_recordings.sh` runs every 2 hours (via launchd/cron) and strips commercials
from any completed show recording that hasn't been processed yet.

```bash
# Runs automatically — trigger manually at any time:
bash ~/ticket-recorder/process_recordings.sh

# Dry-run: show what would be processed
bash ~/ticket-recorder/process_recordings.sh --dry-run

# Process only today's files
bash ~/ticket-recorder/process_recordings.sh --since $(date +%Y-%m-%d)

# Pass custom detection flags
bash ~/ticket-recorder/process_recordings.sh --threshold 0
```

The script skips files that are:
- Currently being written by ffmpeg (`lsof` check)
- Already processed (`_clean.aac` exists)
- Modified within the last 120 seconds (race-condition buffer)

Install/remove the 2-hourly schedule:

```bash
cd ~/ticket-recorder
./deploy.sh schedule-processor --output-dir ~/Dropbox/TheTicket
./deploy.sh unschedule-processor
```

---

## Special event workflow (overnight recordings)

For long overnight events like Campound that span multiple sleep windows:

**1. Add to `overrides.json`** (takes effect within 60 seconds, no restart):
```json
{ "name": "Campound_2026_Night_1", "start": "2026-05-13 19:00", "end": "2026-05-14 06:00" }
```

**2. Recording happens automatically.** The recorder wakes from the Fox Sports Radio
sleep window, records through midnight, and stops at 6 AM.

**3. A cron job fires 5 minutes after end time** and runs an automatic dry-run,
then sends a macOS notification so you can review before cutting:
```bash
# Add one-shot cron entries (replace dates as needed)
5 6 14 5 * /bin/bash ~/ticket-recorder/campound_dryrun.sh \
  ~/Dropbox/TheTicket/2026-05-13_1900_Campound_2026_Night_1.aac
```

**4. After reviewing the dry-run log**, strip for real:
```bash
python3 ~/ticket-recorder/strip_commercials.py \
  ~/Dropbox/TheTicket/2026-05-13_1900_Campound_2026_Night_1.aac --threshold 0
```

**Special events are never deleted by cleanup** — `cleanup.py --keep-special`
preserves any file whose show name isn't in the regular schedule.

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
Historically appeared when the Triton CDN delivered buffered audio faster than realtime at
show boundaries, causing a rapid loop of tiny tail segments. This is fixed in v1.0.4+
(`MIN_SEGMENT_SECS=30` sleeps to the boundary instead of recording a sub-30-second tail).
If you see fragments after upgrading, remove them manually:

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

## Publishing to Transistor.fm

There are two separate publishing scripts serving two different feeds:

| | `transistor_post.py` | `segment_post.py` |
|---|---|---|
| **Purpose** | Full show episodes (automated) | Highlight clips (manual) |
| **Feed** | The Ticket | The Ticket Archive |
| **Source** | VPS `~/recordings/` | `~/Dropbox/The Ticket/` |
| **Trigger** | Cron every 30 min | Run by hand |
| **WordPress** | No | Yes — also posts to theunticket.com |

### `transistor_post.py` — automated full episodes (VPS)

Runs every 30 minutes on the VPS and handles the full post-recording pipeline:
1. Finds finished `.aac` recordings (skips files still being written or modified in the last 2 minutes)
2. Converts each to MP3 at 128 kbps via ffmpeg, verifies with ffprobe
3. Deletes the source AAC only after successful MP3 verification
4. Uploads the MP3 to Transistor.fm (**The Ticket** show) and publishes with the recording date

Episode title format: **`Show Name - MM.dd.YYYY`** (e.g. `The Musers - 05.19.2026`)

Deduplication: loads all existing episode titles from Transistor at startup and maintains a local `transistor_uploaded.json` state file. Re-runs are safe.

```bash
# Store API key on VPS
echo 'TRANSISTOR_API_KEY=your_key_here' > ~/.transistor_env
chmod 600 ~/.transistor_env

# Add cron job (every 30 min)
(crontab -l; echo '*/30 * * * * set -a && . /home/ticket/.transistor_env && set +a && python3 /home/ticket/ticket-recorder/transistor_post.py --dir /home/ticket/recordings >> /home/ticket/ticket-recorder/logs/transistor.log 2>&1') | crontab -

# Manual run
python3 transistor_post.py --dir ~/recordings
python3 transistor_post.py --file ~/recordings/2026-05-19_0600_The_Musers.mp3
python3 transistor_post.py --dry-run --dir ~/recordings
```

### `segment_post.py` — manual highlight clips (Mac)

Interactive CLI run locally to post individual segments from `~/Dropbox/The Ticket/` to both:
- **The Ticket Archive** on Transistor.fm
- **theunticket.com** (WordPress) — uploads the MP3 to the media library and creates a post with an `[audio]` player

```bash
# Standard post (Transistor + WordPress)
python3 segment_post.py

# Transistor only
python3 segment_post.py --transistor-only

# WordPress only (e.g. after partial failure)
python3 segment_post.py --wp-only --file 'path/to/file.mp3'

# Preview without posting
python3 segment_post.py --dry-run
```

Credentials stored in `~/.segment_post_env`. Tags default by show folder and are saved per-show to `~/.segment_post_tags.json`. Transistor publish times auto-increment by 1 minute on the same date to maintain feed ordering. See [`docs/runbook.md`](docs/runbook.md) for the full reference.
---
## VPS management quick reference

SSH in with:
```bash
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199
```

| Task | Command |
|---|---|
| Check service | `sudo systemctl status ticket-recorder` |
| Live logs | `sudo journalctl -u ticket-recorder -f` |
| Restart recorder | `sudo systemctl restart ticket-recorder` |
| View recorder log | `tail -50 ~/ticket-recorder/logs/recorder.log` |
| Check today's recordings | `ls -lh ~/recordings/$(date +%Y-%m-%d)* 2>/dev/null` |
| Force rclone sync now | `rclone copy ~/recordings dropbox:TheTicket --progress` |
| Check rclone log | `tail -20 ~/ticket-recorder/logs/rclone.log` |
| Check disk space | `df -h ~ && du -sh ~/recordings/` |
| View cron jobs | `crontab -l` |
| Update recorder code | `cd ~/ticket-recorder && git pull && sudo systemctl restart ticket-recorder` |
| Edit overrides | `nano ~/ticket-recorder/overrides.json` |

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
├── transistor_post.py     # Auto-publisher: AAC→MP3, posts full episodes to 'The Ticket' on Transistor
├── segment_post.py        # Interactive: post highlight clips to 'The Ticket Archive' + theunticket.com
├── strip_commercials.py   # Commercial detection and removal (dry-run safe)
├── merge_segments.py      # Join split recordings from recorder restarts (lossless)
├── process_recordings.sh  # 2-hourly automated post-processing pipeline (--merge flag)
├── campound_dryrun.sh     # One-shot dry-run + notification for overnight specials
├── cleanup.py             # Retention policy — delete old recordings, keep special events
├── monitor_health.py      # 15-min health check — service, file freshness, disk space alerts
├── monitor_transition.py  # Show rollover monitor — watch for new files, print banners
├── overrides.json         # Custom recording events (edit anytime, no restart needed)
├── aac2mp3                # CLI tool: convert AAC → MP3 with ffprobe verify + optional source delete
├── deploy.sh              # macOS (launchd) and Linux (systemd/cron) service installer
├── verify_recording.py    # End-to-end pipeline verification (stream, segment, SIGTERM)
├── Dockerfile             # Container image definition
├── docker-compose.yml     # Compose service configuration
├── test_override_sleep.py # Pytest test suite (14 tests)
├── test_full_pipeline.py  # Integration test — recording + cleanup (18 checks)
├── TESTING.md             # What's verified vs still needs real-world validation
├── docs/
│   ├── vps-deployment.md  # Full remote VPS setup guide (DigitalOcean/Hetzner + rclone + systemd)
│   └── vps/               # Live config files deployed on the VPS
│       ├── ticket-recorder.service   # systemd unit
│       ├── logrotate-ticket-recorder # log rotation (daily, 14-day retention)
│       └── crontab                   # rclone copy every 15 min + nightly cleanup
├── logs/                  # Created by deploy.sh — recorder.log, cleanup.log, monitor.log
└── recordings/            # Default output dir for Docker Compose
```

Detailed guides are in [`docs/`](docs/):
- [`docs/vps-deployment.md`](docs/vps-deployment.md) — full remote VPS setup (provisioning, systemd, rclone, Dropbox auth, Tailscale)
- [`docs/Deployment.md`](docs/Deployment.md) — macOS, Linux, and Docker step-by-step
- [`docs/Maintenance.md`](docs/Maintenance.md) — daily runbook, special events, split-recording recovery
- [`docs/Commercial-Detection.md`](docs/Commercial-Detection.md) — tuning `strip_commercials.py` for different show types

### macOS deployment note

For production use, run the service from a copy **outside** Dropbox:

```
~/ticket-recorder/     ← service runs from here (launchd-accessible)
~/Dropbox/TheTicket/   ← recordings land here (Dropbox syncs to cloud)
```

Keep the Dropbox copy (`Warp Projects/ticket-recorder/`) as the development repo and git remote.
