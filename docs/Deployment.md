# Deployment Guide

## Current Production Setup (VPS — DigitalOcean)

All recording runs exclusively on the VPS. The local Mac runs only `segment_post.py`
manually to post highlight clips.

### VPS details
| Item | Value |
|---|---|
| Host | `68.183.143.199` |
| User | `ticket` |
| SSH key | `~/.ssh/themarc-digitalocean` |
| Recordings dir | `/home/ticket/recordings/` |
| Project dir | `/home/ticket/ticket-recorder/` |
| Transistor show (full eps) | The Ticket (ID 78274) |
| Transistor show (segments) | The Ticket Archive |
| Dropbox sync target | `dropbox:TheTicket` |

### Services
| Service | Type | Config |
|---|---|---|
| `ticket-recorder` | systemd unit | `sudo systemctl status ticket-recorder` |
| rclone sync | cron `*/15 * * * *` | copies `~/recordings/*.mp3` to Dropbox (AACs excluded) |
| `transistor_post.py` | cron `*/30 * * * *` | converts AAC→MP3, posts to Transistor |
| `cleanup.py` | cron `0 2 * * *` | removes recordings older than 30 days |

### Deploy code changes to VPS
GitHub pull is blocked by network policy; use `scp` instead:
```bash
ssh-add ~/.ssh/themarc-digitalocean
scp -i ~/.ssh/themarc-digitalocean transistor_post.py ticket@68.183.143.199:~/ticket-recorder/
```

### Check status
```bash
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 \
  "sudo systemctl status ticket-recorder --no-pager | grep -E 'Active|Main PID' && tail -5 ~/ticket-recorder/logs/recorder.log"
```

### Restart recorder
```bash
ssh -i ~/.ssh/themarc-digitalocean ticket@68.183.143.199 \
  "sudo systemctl restart ticket-recorder"
```

---

## Local Mac — Disabled

The local macOS LaunchAgents (recorder, monitor, cleanup, processor) are **disabled**.
Plist files are archived in `launchagents-disabled/` for reference.
Do not re-enable them — recording runs on the VPS only.

---

## macOS (launchd) — Legacy / Reference Only

### One-time setup
```bash
# Copy project outside Dropbox — launchd cannot read ~/Library/CloudStorage/
cp -r "/path/to/Warp Projects/ticket-recorder" ~/ticket-recorder
cd ~/ticket-recorder

# Install all four services
./deploy.sh install              --output-dir ~/Dropbox/TheTicket
./deploy.sh schedule-cleanup     --output-dir ~/Dropbox/TheTicket --days 30
./deploy.sh schedule-monitor     --output-dir ~/Dropbox/TheTicket
./deploy.sh schedule-processor   --output-dir ~/Dropbox/TheTicket
```

> The recorder plist uses `caffeinate -i` to prevent Mac idle sleep during recording.
> `MIN_SEGMENT_SECS=90` prevents CDN buffer dumps from creating boundary fragment
> files at show transitions.

---

## Linux (systemd + cron)

### Prerequisites
```bash
sudo apt update && sudo apt install -y python3 ffmpeg git
```

### One-time setup
```bash
git clone https://github.com/marcsalot/ticket-recorder
cd ticket-recorder

./deploy.sh install              --output-dir ~/recordings
./deploy.sh schedule-cleanup     --output-dir ~/recordings --days 30
./deploy.sh schedule-monitor     --output-dir ~/recordings
./deploy.sh schedule-processor   --output-dir ~/recordings

# Persist after SSH logout
sudo loginctl enable-linger $USER
```

### Alternatively — use the static service file

A pre-written `ticket-recorder.service` is included in the repo. Edit the two `EDIT_ME` paths and drop it in:

```bash
# Edit WorkingDirectory and ExecStart paths
nano ticket-recorder.service

mkdir -p ~/.config/systemd/user
cp ticket-recorder.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ticket-recorder
sudo loginctl enable-linger $USER
```

### Verify
```bash
systemctl --user status ticket-recorder   # or com.theticket.recorder if using deploy.sh
journalctl --user -u ticket-recorder -n 20
crontab -l   # shows cleanup, monitor, processor entries
```

---

## Docker (cloud/VPS)

```bash
git clone https://github.com/marcsalot/ticket-recorder
cd ticket-recorder

# Edit docker-compose.yml to set recording path, then:
docker compose up -d
docker compose logs -f
```

Recordings go to `./recordings/` on the host. `overrides.json` is bind-mounted — edit it on the host and the container picks it up within 60 seconds.
