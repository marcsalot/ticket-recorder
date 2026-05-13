# Maintenance Runbook

## Daily (automated — no action needed)

| Time CDT | What happens |
|---|---|
| 5:30 AM | Recorder wakes, starts The Musers Pregame |
| Throughout day | Shows record automatically, processor strips commercials every 2h |
| 2:00 AM | Cleanup deletes recordings older than 30 days (keeps special events) |

---

## Weekly health check

```bash
# All four services loaded and healthy?
launchctl list | grep theticket

# Any recording errors this week?
grep -i error ~/ticket-recorder/logs/recorder.error.log | tail -20

# Cleanup ran successfully?
tail -20 ~/ticket-recorder/logs/cleanup.log

# Processor ran successfully?
tail -20 ~/ticket-recorder/logs/processor.log

# Disk space
df -h ~/Dropbox
```

---

## Adding a special event (playoffs, Campound, etc.)

Edit `overrides.json` — changes take effect within **60 seconds**, no restart needed:

```bash
nano ~/ticket-recorder/overrides.json
```

Format:
```json
{ "name": "Stars_Playoff_Game_1", "start": "2026-06-01 18:30", "end": "2026-06-01 23:30" }
```

All times in **Central Time**. For overnight events set `end` to the next calendar day.

Special-event files are automatically preserved forever by the cleanup job (`--keep-special`).

### For multi-night events (Campound pattern)

1. Add both nights to `overrides.json`
2. Add one-shot cron entries to auto-run the dry-run at end time:
```bash
# Night 1 ends at 6 AM May 14 — dry-run at 6:05 AM
5 6 14 5 * /bin/bash ~/ticket-recorder/campound_dryrun.sh \
  ~/Dropbox/TheTicket/2026-05-13_1900_EventName_Night_1.aac
```
3. After reviewing the dry-run log, strip for real:
```bash
python3 ~/ticket-recorder/strip_commercials.py \
  ~/Dropbox/TheTicket/2026-05-13_1900_EventName_Night_1.aac --threshold 0
```

---

## Handling a failed/split recording

If the recorder crashed mid-show and restarted, you'll have two files:
```
2026-05-13_0600_The_Musers.aac    ← before crash
2026-05-13_0632_The_Musers.aac    ← resumed
```

Fix with one command:
```bash
# Preview
python3 ~/ticket-recorder/merge_segments.py ~/Dropbox/TheTicket --dry-run

# Merge (lossless)
python3 ~/ticket-recorder/merge_segments.py ~/Dropbox/TheTicket

# Then process normally
bash ~/ticket-recorder/process_recordings.sh --merge --output-dir ~/Dropbox/TheTicket
```

⚠ Do NOT run merge on directories with `--segment-minutes` recordings.

---

## Manually running the processor

```bash
# Process all unprocessed completed shows
bash ~/ticket-recorder/process_recordings.sh --output-dir ~/Dropbox/TheTicket

# Preview only (no files changed)
bash ~/ticket-recorder/process_recordings.sh --dry-run

# Tonight's files only
bash ~/ticket-recorder/process_recordings.sh --since $(date +%Y-%m-%d)

# Merge split recordings first, then process
bash ~/ticket-recorder/process_recordings.sh --merge
```

---

## If the schedule changes on theticket.com

```bash
# Fetch current schedule
curl -sL "https://www.theticket.com/full-programming-schedule" | \
  python3 -c "import sys,re,html as h; t=sys.stdin.read(); t=re.sub('<[^>]+>',' ',t); [print(h.unescape(l.strip())) for l in t.split('\n') if re.search(r'\d{1,2}:\d{2}\s*(a\.m\.|p\.m\.)',l)]"
```

Update two files:
1. `recorder.py` → `SHOW_SCHEDULE` (times and show names)
2. `cleanup.py` → `REGULAR_SHOW_NAMES` (add any new show name slugs)

Then reinstall:
```bash
cd ~/ticket-recorder
./deploy.sh uninstall && ./deploy.sh install --output-dir ~/Dropbox/TheTicket
```

---

## Restarting services

```bash
# Recorder only
launchctl unload ~/Library/LaunchAgents/com.theticket.recorder.plist
launchctl load -w ~/Library/LaunchAgents/com.theticket.recorder.plist

# Full reinstall (picks up code changes)
cd ~/ticket-recorder
./deploy.sh uninstall && ./deploy.sh install --output-dir ~/Dropbox/TheTicket
```

---

## Troubleshooting quick reference

| Symptom | First check |
|---|---|
| No new recordings | `launchctl list \| grep recorder` — PID should be non-zero |
| Fragment files (`_2.aac`, `_3.aac`) | Fixed in v1.0.4+; delete with `rm ~/Dropbox/TheTicket/*_[0-9].aac` |
| Processor not running | `tail ~/ticket-recorder/logs/processor.log` |
| Override not picking up | Check datetime format — must be `YYYY-MM-DD HH:MM` Central Time |
| Disk space warning | `cleanup.py --dry-run` to see what's accumulating |
| Commercial strip missed breaks | Run with `--threshold 0` (clustering only) and tune `--max-break` |
