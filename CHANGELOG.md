# Changelog

All notable changes to this project are documented here.

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
