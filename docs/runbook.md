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
6. **Transistor date / time** — original air date pre-filled from filename; Enter to accept
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

### Partial failure recovery

If one side fails the script prints an exact retry command, e.g.:

```
  Transistor succeeded (episode ID 3278937).
  Retry WordPress with:
    python3 segment_post.py --wp-only --file 'path/to/file.mp3'
```

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
