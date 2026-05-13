# Deployment Guide

## macOS (launchd)

### Prerequisites
```bash
brew install python ffmpeg
```

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

### Verify
```bash
launchctl list | grep theticket
# Expected:
#   <PID>  0  com.theticket.recorder    ← non-zero PID = running
#   -      0  com.theticket.monitor     ← scheduled
#   -      0  com.theticket.cleanup     ← scheduled
#   -      0  com.theticket.processor   ← scheduled
```

### Service files and logs

| Service | Plist | Log |
|---|---|---|
| Recorder | `~/Library/LaunchAgents/com.theticket.recorder.plist` | `logs/recorder.log` |
| Cleanup | `~/Library/LaunchAgents/com.theticket.cleanup.plist` | `logs/cleanup.log` |
| Monitor | `~/Library/LaunchAgents/com.theticket.monitor.plist` | `logs/monitor.log` |
| Processor | `~/Library/LaunchAgents/com.theticket.processor.plist` | `logs/processor.log` |

> The recorder plist uses `caffeinate -i` to prevent Mac idle sleep during recording.
> This is critical for overnight events. `MIN_SEGMENT_SECS=90` prevents CDN buffer
> dumps from creating boundary fragment files at show transitions.

### Update after code changes
```bash
cd ~/ticket-recorder
git pull   # if using a local clone, otherwise copy files manually
./deploy.sh uninstall && ./deploy.sh install --output-dir ~/Dropbox/TheTicket
```

### Remove everything
```bash
./deploy.sh uninstall
./deploy.sh unschedule-cleanup
./deploy.sh unschedule-monitor
./deploy.sh unschedule-processor
```

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
