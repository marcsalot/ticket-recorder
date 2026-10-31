# The Ticket & YouTube Recorder Runbook

Operations reference for the KTCK stream recorder (VPS) and YouTube audio downloader.

---

## SSH Setup

Add key to agent before any VPS commands (required after each reboot or session):

```shell
ssh-add ~/.ssh/themarc-digitalocean
```

---

## The Ticket Recorder — VPS

### Check recorder status

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "sudo systemctl status ticket-recorder --no-pager | grep -E 'Active|Main PID' && tail -5 ~/ticket-recorder/logs/recorder.log"
```

### Check today's recordings

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "ls -lh ~/recordings/$(date +%Y-%m-%d)* 2>/dev/null || echo 'No recordings yet today'"
```

### Check all recordings on VPS

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "ls -lh ~/recordings/"
```

### Force sync to Dropbox now

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "rclone copy ~/recordings dropbox:TheTicket --progress"
```

### Check Transistor posting log

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "tail -30 ~/ticket-recorder/logs/transistor.log | grep -v 'Still recording\|Too recent\|Looking up\|Found show\|Loaded'"
```

### Check disk space

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "df -h / && du -sh ~/recordings/"
```

### Restart recorder after config change

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "cd ~/ticket-recorder && git pull && sudo systemctl restart ticket-recorder"
```

### Edit overrides (special events like Campound)

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "nano ~/ticket-recorder/overrides.json"
```

### View cron jobs

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "crontab -l"
```

---

## The Ticket Recorder — Post missed episode to Transistor

Use this when an episode shows `"existing"` in the state file but isn't actually on Transistor.

### Clear stale state entry and re-queue

Replace the filename stem (e.g. `2026-05-20_0600_The_Musers`) with the correct one:

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "python3 -c \"
import json
from pathlib import Path
sf = Path('/home/ticket/ticket-recorder/transistor_uploaded.json')
s = json.loads(sf.read_text())
s.pop('{{episode_stem}}', None)
sf.write_text(json.dumps(s, indent=2))
print('Cleared — will post on next cron run')
\""
```

### Manually trigger Transistor post now

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "set -a && . ~/.transistor_env && set +a && python3 ~/ticket-recorder/transistor_post.py --dir ~/recordings"
```

### Post a specific MP3 file

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "set -a && . ~/.transistor_env && set +a && python3 ~/ticket-recorder/transistor_post.py --file ~/recordings/{{filename}}.mp3"
```

---

## The Ticket Recorder — Local Mac tools

### Convert AAC to MP3 (whole folder, delete source)

```shell
aac2mp3 ~/Dropbox/TheTicket/ --delete-source
```

### Convert a single file

```shell
aac2mp3 {{file.aac}} --delete-source
```

### Preview what aac2mp3 would convert

```shell
aac2mp3 ~/Dropbox/TheTicket/ --dry-run
```

---

## Segment Post Tool

Posts an audio segment from `~/Dropbox/The Ticket/` to both Transistor.fm
(show: **The Ticket Archive**) and theunticket.com (WordPress) in one step.

### First-time setup

Fill in credentials in `~/.segment_post_env`:

```shell
nano ~/.segment_post_env
```

```
TRANSISTOR_API_KEY=your_key
WP_URL=https://www.theunticket.com
WP_USER=your_wp_username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

Generate a WordPress Application Password at:
**WP Admin → Users → Your Profile → Application Passwords**

### Post a segment (standard — Transistor + WordPress)

```shell
python3 ~/ticket-recorder/segment_post.py
```

Prompt order — press Enter to accept every default for a fast post:

1. Pick MP3 from numbered list (newest-first, with show folder)
2. **Title** — auto-filled as `Segment Name - Show Folder M.D.YYYY`; edit or Enter
3. **Description** — optional single line; Enter to skip
4. **Tags** — show-specific defaults displayed; Enter to keep, or `+Tag, -Tag` to edit
   - First edit is offered to save as the new default for that show
5. **WordPress category** — defaults to show folder name; Enter to accept
6. **Transistor date / time** — original air date pre-filled from filename
   - If the last post was on the **same date**, time defaults to last post + 1 minute (keeps feed order)
   - First post of a new date defaults to 12:00 CT
   - WordPress always posts at current UTC time (no scheduling)
7. Review summary and confirm → uploads to Transistor, then uploads MP3 and creates WP post

### Editing tags inline

At the tags prompt, type edits as a single comma-separated line:

```
  Edit (+add / -remove, Enter to keep): +Donovan Lewis, -KTCK
```

First time editing tags for a show, you'll be asked to save them as the new default.
Saved defaults are stored in `~/.segment_post_tags.json` and used automatically next time.

### Default tags by show

| Show folder | Tags |
|---|---|
| The Musers | The Musers, Musers, Gordon Keith, George Dunham, Craig Miller, KTCK |
| The Hardline | The Hardline, Hardline, Corby Davidson, Bob Sturm, Dave Lane, KTCK |
| The Sweet Spot | The Sweet Spot, Sweet Spot, Sean Bass, Mino, Monty, KTCK |
| The Invasion | The Invasion, Invasion, Donovan Lewis, Matt McClearin, KTCK |
| Work in Progress | Work in Progress, WIP, DJ, Monty, KTCK |
| The Rant | The Rant, Rant, Gordon Keith, Dave Lane, Corby Davidson, KTCK |
| BaD Radio | BaD Radio, BaD, Bob Sturm, Dan McDowell, KTCK |
| *(other)* | The Ticket, KTCK |

### Post to Transistor only

```shell
python3 ~/ticket-recorder/segment_post.py --transistor-only
```

### Post to WordPress only (e.g. retry after partial failure)

```shell
python3 ~/ticket-recorder/segment_post.py --wp-only --file '{{path/to/file.mp3}}'
```

### Post WordPress as draft

```shell
python3 ~/ticket-recorder/segment_post.py --draft
```

### Skip the file picker

```shell
python3 ~/ticket-recorder/segment_post.py --file '{{path/to/file.mp3}}'
```

### Dry-run (full prompt flow, nothing posted)

```shell
python3 ~/ticket-recorder/segment_post.py --dry-run
```

### Check segment post log

```shell
tail -30 ~/ticket-recorder/logs/segment_post.log
```

### Edit saved tag defaults

```shell
nano ~/.segment_post_tags.json
```

### Reset / check the last Transistor publish time

View or edit `~/.segment_post_state.json` to adjust the last publish time
if you need to re-sequence posts:

```shell
cat ~/.segment_post_state.json
```

```shell
nano ~/.segment_post_state.json
```

### Partial failure recovery

If one side fails the script prints an exact retry command, e.g.:

```
  Transistor succeeded (episode ID 3278937).
  Retry WordPress with:
    python3 segment_post.py --wp-only --file 'path/to/file.mp3'
```

### Disable WordPress posting permanently

Add to `~/.segment_post_env` to skip WordPress on every run (Transistor-only mode):

```
WP_ENABLED=0
```

Remove or set to `1` to re-enable. The `--transistor-only` flag still works per-run.

### Auto-tag rules (filename-based)

The script automatically injects tags based on keywords in the episode title/filename.
Rules live in `AUTO_TAG_RULES` in `segment_post.py`.

Examples of what fires automatically:

| Keyword in title | Tags added |
|---|---|
| `wtds` | `wtds` |
| `trifecta` | `Donovan Lewis`, `trifecta` |
| `ebrake` or `e-brake` | `ebrake` |
| `birdhouse` | `Birdhouse`, `Corby Davidson` |
| `afternoon jub` | `afternoon jub`, `Afternoon Jub`, `George Dunham` |
| `fake`, `touchdown bros`, `dirty doug out`, etc. | `840 bit` |
| `odeck` | `observation deck` |

Filename aliases (rename the title before posting):

| Filename contains | Becomes in title |
|---|---|
| `odeck` | Observation Deck |
| `FF` (whole word) | Friday Fun |
| `GYM` (whole word) | Generic Youth Minister |
| `CUNT` | C.U.N.T. |

To add a new rule, append to `AUTO_TAG_RULES` in `segment_post.py` and commit.

---

## Backfill Tags — Retroactive Tagging

Applies current `AUTO_TAG_RULES` to all existing Transistor Archive episodes.

### Full backfill (~2.5 hours)

```shell
python3 ~/ticket-recorder/backfill_tags.py
```

### Preview first (no changes)

```shell
python3 ~/ticket-recorder/backfill_tags.py --dry-run
```

### Fast backfill — only previously unmatched episodes (~30–90 min)

Requires `backfill_unknowns.json` from a previous full run:

```shell
python3 ~/ticket-recorder/backfill_tags.py --from-cache
```

### Tag episodes by a single keyword

Search all titles for a keyword and add a tag — no rule needed:

```shell
# Keyword becomes the tag (lowercase)
python3 ~/ticket-recorder/backfill_tags.py --keyword "norm hitzges"

# Specify a different display tag
python3 ~/ticket-recorder/backfill_tags.py --keyword "mike rhyner" --tag "Mike Rhyner"

# Preview first
python3 ~/ticket-recorder/backfill_tags.py --keyword "mike rhyner" --tag "Mike Rhyner" --dry-run
```

### Run in background (safe to close tab)

```shell
nohup python3 -u ~/ticket-recorder/backfill_tags.py --keyword "norm hitzges" > ~/ticket-recorder/logs/kw_norm.log 2>&1 &
echo PID:$!
```

Check progress:

```shell
tail -10 ~/ticket-recorder/logs/kw_norm.log
```

After each run, `backfill_unknowns.json` is saved with recurring unmatched segment names — a list for discovering new rules.

---

## theticketarchive.net

Next.js site on the VPS displaying the Transistor Archive.

### Check site status

```shell
curl -s -o /dev/null -w "%{http_code}" https://theticketarchive.net/
```

### Restart site

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "
OLD=\$(ss -tlnp | grep ':3000' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n \"\$OLD\" ] && sudo kill \$OLD && sleep 2
sudo -u ticket bash -c 'cd /var/www/ticket-archive && nohup npm run start > /tmp/ticket-archive.log 2>&1 &'
"
```

### Resync episode cache (refresh tags/data from Transistor)

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "
cd /var/www/ticket-archive && sudo bash -c 'set -a && . .env.local && set +a && node scripts/sync.mjs'
"
```

### Rebuild after code changes

```shell
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 "cd /var/www/ticket-archive && sudo npm run build"
```

### Site URLs

| Page | URL |
|---|---|
| Home (newest) | `https://theticketarchive.net/` |
| Home (oldest first) | `https://theticketarchive.net/?sort=asc` |
| Archive by tag/personality | `https://theticketarchive.net/archive` |
| Shows index | `https://theticketarchive.net/shows` |
| Podcast feeds | `https://theticketarchive.net/feed` |
| Per-show RSS feed | `https://theticketarchive.net/api/feed/the-hardline` |
| Search | `https://theticketarchive.net/search?q=ebrake` |

---

## YouTube Audio Recorder

Script location: `~/ticket-recorder/youtube_recorder.py`
Default output: `~/Downloads/YouTube Audio/`

### Download a video (dry run first)

```shell
python3 ~/ticket-recorder/youtube_recorder.py {{url}} --dry-run
```

### Download a video

```shell
python3 ~/ticket-recorder/youtube_recorder.py {{url}}
```

### Download to a named subfolder

```shell
python3 ~/ticket-recorder/youtube_recorder.py {{url}} --folder "{{folder_name}}"
```

### Download and group by channel name

```shell
python3 ~/ticket-recorder/youtube_recorder.py {{url}} --by-channel
```

### Record a live stream

```shell
python3 ~/ticket-recorder/youtube_recorder.py {{url}} --live
```

### Download to a custom directory

```shell
python3 ~/ticket-recorder/youtube_recorder.py {{url}} --output-dir {{output_dir}}
```

### Install / update dependencies

```shell
brew install yt-dlp ffmpeg && yt-dlp --version && ffmpeg -version | head -1
```

---

## Useful reference

| Item | Value |
|---|---|
| VPS IP | `68.183.143.199` |
| VPS user | `ticket` |
| SSH key | `~/.ssh/themarc-digitalocean` |
| Recordings dir (VPS) | `/home/ticket/recordings/` |
| Transistor show (auto) | The Ticket (ID 78274) |
| Transistor show (segments) | The Ticket Archive |
| Dropbox folder | `~/Dropbox/The Ticket/` |
| Transistor log | `/home/ticket/ticket-recorder/logs/transistor.log` |
| State file | `/home/ticket/ticket-recorder/transistor_uploaded.json` |
| Segment post credentials | `~/.segment_post_env` |
| Segment post log | `~/ticket-recorder/logs/segment_post.log` |
| WordPress site | `https://www.theunticket.com` |
| Archive site | `https://theticketarchive.net` |
| Archive site source | `/var/www/ticket-archive/` on VPS |
| Backfill unknowns cache | `~/ticket-recorder/backfill_unknowns.json` |
| Segment auto-tag rules | `AUTO_TAG_RULES` in `segment_post.py` |
