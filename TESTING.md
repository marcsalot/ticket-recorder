# Testing Status

Tracks what has been verified against the live KTCK stream and what still
needs real-world validation before the system can be considered fully proven.

---

## Verified ✔

### Core recording
- Stream connectivity — live AAC stream captured at correct bitrate (~48 kbps)
- Show-boundary segmentation — rollovers confirmed live at 1:00 PM and 3:00 PM CDT
- Graceful SIGTERM — partial file saved and closed correctly
- `start_new_session` ffmpeg isolation — no file corruption on shutdown
- Same-minute filename collision — `_2`, `_3` counter prevents overwrites
- Overnight sleep → override wake-up — 14 unit tests + integration test passing

### Cleanup
- `--dry-run` correctly reports `WOULD DELETE` vs `KEPT(special)` without touching files
- Live deletion of old regular files while preserving special-event recordings
- `--keep-special` detection by filename slug (no external tracking file needed)

### Commercial detection
- `strip_commercials.py` dry-run on partial Hardline (56 min): correctly identified
  fragmentation-cluster commercial breaks, 0 false positives observed
- End-to-end strip on The Invasion: 26 MB → 17 MB (39% removed)
- End-to-end strip on The Sweet Spot: 42 MB → 29 MB (34% removed)
- `lsof` skip correctly prevented processing The Hardline while recording

### Automation
- `process_recordings.sh` — skips active files, skips already-processed, processes completed
- All four launchd services loaded: recorder, monitor, cleanup, processor
- macOS notifications fire on `--test` and on real processing completion
- Health check: service, file freshness, disk space all reporting correctly

### Automated tests
- `test_override_sleep.py` — 14/14 passing
- `verify_recording.py` — 3/3 passing (stream + SIGTERM + full segment)
- `test_full_pipeline.py` — 18/18 passing

---

## Still needs real-world validation ✘

### Tonight (Tue 2026-05-12)
- **The Hardline full recording (3–7 PM CDT)** — first complete 4-hour show.
  Run strip_commercials dry-run to check detection quality across a full show:
  ```bash
  python3 ~/ticket-recorder/strip_commercials.py \
    ~/Dropbox/TheTicket/2026-05-12_1500_The_Hardline.aac --dry-run
  ```
  Expected: ~6 commercial breaks, ~30–40 min removed from a 240-min show.
  If detection looks off: tune `--threshold` or `--max-break` and re-run.

- **2 AM cleanup job** — first automated run. Verify after 2 AM CDT (7 AM UTC):
  ```bash
  cat ~/ticket-recorder/logs/cleanup.log
  ```
  Expected: log entry showing files scanned, any >30-day files deleted,
  special events kept.

### This week
- **Overnight sleep → morning wake-up** — recorder sleeps at 7 PM CDT, should
  wake at 5:30 AM Wednesday to record The Musers Pregame. Verify:
  ```bash
  # Wednesday morning ~5:30 AM CDT
  tail -5 ~/ticket-recorder/logs/recorder.log
  ls ~/Dropbox/TheTicket/2026-05-13_*.aac
  ```

- **Full day's recordings processed automatically** — leave the system alone for
  one full weekday and verify the processor's 2-hourly runs create `_clean.aac`
  files without intervention.

- **Processor re-run protection** — verify that running the processor a second
  time correctly skips already-processed files (`SKIP (already processed)` log).

### This week-end
- **Saturday schedule** — The Teebox, College Football Tailgate, Afternoon Delight,
  The Kick Around should all record. Fox Sports Radio slots should be skipped.
  Check Saturday morning:
  ```bash
  python3 ~/ticket-recorder/monitor_transition.py --stop-after 10:05
  ```

- **Sunday schedule** — Mark Elfenbein (7–9 AM), Work in Progress (9 AM–noon).

### When a special event occurs
- **overrides.json live test** — add a real Cowboys/Stars event and confirm:
  1. Recorder wakes from Fox Sports Radio sleep within 60 seconds
  2. File is named after the override entry
  3. `--keep-special` prevents cleanup from deleting it
  4. Commercial strip runs on the override file via the processor

### Long-term (30 days)
- **Cleanup rotation** — first time recordings older than 30 days are deleted.
  Confirm special-event files survive and regular shows rotate correctly.
- **Dropbox space** — verify steady-state footprint stays near the projected 7 GB.

---

## Commercial detection tuning notes

Default parameters (as of current commit) applied to today's partial recordings:

| File | Duration | Raw | Clean | Removed |
|---|---|---|---|---|
| The_Invasion | ~75 min | 26 MB | 17 MB | 39% |
| The_Sweet_Spot | ~100 min | 42 MB | 29 MB | 34% |
| The_Hardline | not yet complete | — | — | — |

**If the Hardline dry-run shows missed breaks:**
- Try `--max-break 540` (9 min) for very long commercial blocks
- Try `--threshold 0` to rely on clustering alone (no RMS gate)

**If the dry-run shows false positives (show content being cut):**
- Try `--min-break 90` (raise minimum break length)
- Try `--max-spot 30` (tighter definition of a "commercial sub-segment")
- Try `--threshold 5` (stricter RMS gate)

Once you've dialled in parameters that work consistently across a full week
of Hardline recordings, update `STRIP_ARGS` in `process_recordings.sh` to
pass them automatically.
