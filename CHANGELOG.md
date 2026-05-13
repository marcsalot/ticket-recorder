# Changelog

All notable changes to this project are documented here.

---

## v1.0.6 — 2026-05-13

**Self-healing improvements**
- `recorder.py`: `MIN_SEGMENT_SECS` raised 30→90 to cover CDN buffer dumps ~50s
  before show boundaries (observed on multiple transitions); prevents boundary
  fragment loop from ever forming.
- `deploy.sh`: recorder plist wraps python3 with `caffeinate -i` to prevent Mac
  idle sleep during recording (critical for 11-hour Campound-style overnights).
- `monitor_health.py`: 4th check — stream activity via byte-growth sampling over
  15 seconds; detects silent/stalled streams that still update file mtime.
  Skips files < 60s old to avoid false positives during ffmpeg init buffer.
- `recorder.py`: `validate_overrides()` called at startup; logs structured
  WARNING for JSON errors, missing fields, bad datetime format, and reversed
  start/end. Also adds `--validate` CLI flag (exit 0 = ok, exit 1 = issues).

**New files**
- `ticket-recorder.service` — static systemd user unit template for Linux
  deployment without running deploy.sh. Edit two `EDIT_ME` paths and copy to
  `~/.config/systemd/user/`.

**Docs**
- `docs/Deployment.md`: Linux section adds static service file instructions
  and notes caffeinate/MIN_SEGMENT_SECS context.

---

## v1.0.4 — 2026-05-12

**Commercial detection + automated processing pipeline**
- `strip_commercials.py` — two-signal detector: fragmentation clustering (dense short-segment
  clusters = commercial break) + optional RMS gate (show content is louder on KTCK).
  Outputs `ShowName_clean.aac` alongside the original via ffmpeg concat demuxer.
- `process_recordings.sh` — automated post-processing: runs every 2 hours, skips files
  currently open by ffmpeg (`lsof` check), skips already-processed files, passes tuning
  flags through to `strip_commercials.py`. macOS notifications on completion/error.
- `monitor_health.py` — 15-minute health checks (service PID, file freshness, disk space)
  with macOS `osascript` notifications. Schedule-aware: silent during skipped slots.
- `deploy.sh` additions: `schedule-processor`, `unschedule-processor`, `schedule-monitor`,
  `unschedule-monitor` (macOS launchd + Linux cron). `MACOS_PLIST` typo fixed.
  `EnvironmentVariables/PATH` added so Homebrew `ffmpeg` is found by launchd.
- `monitor_transition.py` — manual show-rollover watcher with `--stop-after HH:MM`.

**Fixes**
- `run_ffmpeg`: `start_new_session=True` prevents terminal signals racing with Python
  handler and corrupting output files on SIGTERM.
- `make_output_path`: same-minute filename collision counter (`_2`, `_3`, …).
- `cleanup.py _show_name`: strips `_N` counter suffix before matching `REGULAR_SHOW_NAMES`.
- `process_recordings.sh`: empty `STRIP_ARGS` array expansion under `set -u`.

---

## v1.0.0 — 2026-05-12

Initial production release of the KTCK The Ticket stream recorder.

### Added

**Core recorder (`recorder.py`)**
- Continuous live stream recording from KTCK via Triton Digital / StreamTheWorld
- Show-aware segmentation using schedule sourced from theticket.com/full-programming-schedule
- Skips Fox Sports Radio and Ticket Top 10 by default (`"record": false`)
- `overrides.json` hot-reload every 60 seconds — add special events without restarting
- Override detection wakes the recorder from a skipped slot within ~60 seconds
- `--segment-minutes N` splits each show into fixed-length chunks for selective upload
- `--no-schedule` mode for fixed hourly segments
- `start_new_session=True` isolates ffmpeg from terminal signals, preventing file-loss on SIGTERM
- Same-minute filename collision prevention (`Broadcast_2.aac`, `Broadcast_3.aac`, …)
- Graceful SIGTERM/SIGINT shutdown — saves partial file before exiting
- Auto-reconnect on stream drop via ffmpeg `-reconnect` flags; 10-second retry on hard failure

**Retention policy (`cleanup.py`)**
- `--days N` deletes recordings older than N days (default: 30)
- `--keep-special` preserves override/special-event recordings indefinitely by detecting
  show name slugs not present in the regular schedule — no separate tracking file needed
- `--dry-run` previews deletions with `KEPT(special)` / `WOULD DELETE` labels
- Recursive scan handles any future subdirectory structure

**Special event scheduling (`overrides.json`)**
- Evening games during normally-skipped slots
- All-day Saturday coverage
- Overnight recordings spanning midnight (end date on next calendar day)

**Deployment (`deploy.sh`)**
- macOS: launchd user agent (`~/Library/LaunchAgents/com.theticket.recorder.plist`)
- Linux: systemd user unit (`~/.config/systemd/user/com.theticket.recorder.service`)
  with automatic `loginctl enable-linger` for post-logout persistence
- `schedule-cleanup` / `unschedule-cleanup`: installs daily 2 AM cleanup job
  (macOS → launchd, Linux → crontab)
- `--output-dir` and `--days` options for all commands

**Containerised deployment**
- `Dockerfile` (Python 3.11-slim + ffmpeg)
- `docker-compose.yml` with `restart: unless-stopped`, bind-mounted `overrides.json`,
  and structured log rotation

**Tests**
- `test_override_sleep.py` — 14 pytest unit tests covering `sleep_until`, `get_active_override`,
  and override-wakes-recorder integration scenario
- `verify_recording.py` — 3-test end-to-end pipeline: stream connectivity, full segment,
  SIGTERM partial save
- `test_full_pipeline.py` — 4-stage integration test (18 checks): segmented recording
  against live stream, cleanup dry-run, cleanup live run with keep-special, deploy.sh self-check

**Documentation**
- `README.md` — prerequisites, quick start, usage example with real log output, full show
  schedule tables, overrides format, segmenting guide, retention policy reference,
  all three deployment options, stream details, test instructions, project structure
- `CHANGELOG.md` — this file
