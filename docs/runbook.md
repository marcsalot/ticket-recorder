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
| Transistor show | The Ticket (ID 78274) |
| Dropbox folder | `~/Dropbox/TheTicket/` |
| Transistor log | `/home/ticket/ticket-recorder/logs/transistor.log` |
| State file | `/home/ticket/ticket-recorder/transistor_uploaded.json` |
