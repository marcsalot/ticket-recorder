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

```bash
# Install with default output dir (~/ticket_recordings)
./deploy.sh install

# Install with custom output dir
./deploy.sh install --output-dir ~/Dropbox/TheTicket

# Check status, tail logs, remove
./deploy.sh status
./deploy.sh logs
./deploy.sh uninstall
```

Service files and logs are managed automatically:

| Item | Path |
|---|---|
| Launchd plist | `~/Library/LaunchAgents/com.theticket.recorder.plist` |
| stdout log | `ticket-recorder/logs/recorder.log` |
| stderr log | `ticket-recorder/logs/recorder.error.log` |

### Option C — Native Linux (systemd)

Same `deploy.sh` script — it detects the OS and installs a systemd user unit instead.

```bash
./deploy.sh install
./deploy.sh install --output-dir /mnt/storage/TheTicket
```

| Item | Path |
|---|---|
| Unit file | `~/.config/systemd/user/com.theticket.recorder.service` |
| Logs | `journalctl --user -u com.theticket.recorder -f` |

The script calls `loginctl enable-linger` automatically so the service persists after SSH logout. If that requires root, run it manually:

```bash
sudo loginctl enable-linger $USER
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
├── recorder.py            # Main recorder script
├── overrides.json         # Custom recording events (edit anytime)
├── deploy.sh              # Native install for macOS (launchd) and Linux (systemd)
├── Dockerfile             # Container image definition
├── docker-compose.yml     # Compose service configuration
├── test_override_sleep.py # Pytest test suite
├── logs/                  # Created by deploy.sh (native installs only)
└── recordings/            # Default output dir for Docker (./recordings)
```
