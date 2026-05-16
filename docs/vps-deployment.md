# Remote VPS Deployment Guide

Run the recorder on a cheap Linux VPS so your Mac doesn't have to stay on 24/7.
Recordings are synced to Dropbox (or Google Drive) via rclone every 15 minutes.

---

## 1. Provision a VPS

Recommended: either of these from Hetzner's **Cost-Optimized** tier — both have 2 vCPU / 4 GB RAM / 40 GB SSD:

- **CAX11** (~€3.79/mo) — ARM64 (Ampere). Slightly cheaper, runs ffmpeg and Python fine.
- **CX23** (~€3.99/mo) — x86_64 (Intel/AMD). More familiar architecture if you prefer it.

Either works perfectly for this workload. Find them at hetzner.com/cloud/cost-optimized.
Any Ubuntu 22.04 LTS VPS works. 20 GB disk is enough with the 30-day retention policy active (~40 GB/month of MP3s).

Other options: DigitalOcean Droplet ($6/mo), Vultr ($5/mo).

---

## 2. Initial Server Setup

SSH in as root, then:

```bash
# Create a non-root user
adduser ticket
usermod -aG sudo ticket

# Harden SSH (optional but recommended)
sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart sshd

# Switch to your new user
su - ticket
```

---

## 3. Install Dependencies

```bash
sudo apt update && sudo apt install -y \
    python3 python3-pip git ffmpeg curl unzip

# Verify
ffmpeg -version | head -1
python3 --version
```

---

## 4. Clone the Repo

```bash
git clone https://github.com/<your-username>/ticket-recorder.git ~/ticket-recorder
cd ~/ticket-recorder
pip3 install --user -r requirements.txt 2>/dev/null || true
```

---

## 5. Install the Systemd Service

`deploy.sh` handles service installation automatically:

```bash
bash deploy.sh install --output-dir ~/recordings
```

This creates `/etc/systemd/system/ticket-recorder.service` (or a user-level unit) and enables it to start on boot.

To verify:

```bash
systemctl --user status ticket-recorder
journalctl --user -u ticket-recorder -f     # live logs
```

### Manual service file (if deploy.sh is unavailable)

Create `/etc/systemd/system/ticket-recorder.service`:

```ini
[Unit]
Description=KTCK Ticket Stream Recorder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ticket
WorkingDirectory=/home/ticket/ticket-recorder
ExecStart=/usr/bin/python3 -u recorder.py --output-dir /home/ticket/recordings
Restart=always
RestartSec=30
StandardOutput=append:/home/ticket/ticket-recorder/logs/recorder.log
StandardError=append:/home/ticket/ticket-recorder/logs/recorder.error.log

[Install]
WantedBy=multi-user.target
```

```bash
mkdir -p ~/ticket-recorder/logs
sudo systemctl daemon-reload
sudo systemctl enable --now ticket-recorder
```

---

## 6. Install rclone and Configure Cloud Storage

```bash
curl https://rclone.org/install.sh | sudo bash
```

### Option A — Dropbox

```bash
rclone config
# → New remote → name: dropbox → type: dropbox
# → Follow the OAuth URL printed in the terminal
# → On your Mac: open the URL, authorize, paste the token back
```

### Option B — Google Drive (cheaper at ~40 GB/month)

```bash
rclone config
# → New remote → name: gdrive → type: drive
# → Scope: drive.file
# → Follow OAuth flow same as above
```

> **Headless auth tip:** Run `rclone authorize "dropbox"` on your Mac first,
> copy the token, then paste it into the VPS config prompt.

---

## 7. Schedule rclone Sync (Every 15 Minutes)

Add to crontab (`crontab -e`):

```cron
*/15 * * * * /usr/bin/rclone sync /home/ticket/recordings dropbox:TheTicket \
    --log-file /home/ticket/ticket-recorder/logs/rclone.log \
    --log-level INFO 2>&1
```

Replace `dropbox:TheTicket` with `gdrive:TheTicket` if using Google Drive.

Verify a sync manually:

```bash
rclone sync ~/recordings dropbox:TheTicket --progress
```

---

## 8. Schedule Cleanup (Daily at 2 AM)

```bash
bash ~/ticket-recorder/deploy.sh schedule-cleanup \
    --output-dir ~/recordings --days 30
```

Or add manually to crontab:

```cron
0 2 * * * python3 /home/ticket/ticket-recorder/cleanup.py \
    --output-dir /home/ticket/recordings --days 30 --keep-special
```

---

## 9. Common Management Commands

```bash
# Check status
systemctl --user status ticket-recorder

# Restart after config change
systemctl --user restart ticket-recorder

# Live logs
journalctl --user -u ticket-recorder -f

# Last 50 log lines
tail -50 ~/ticket-recorder/logs/recorder.log

# Check what's been recorded today
ls -lh ~/recordings/ | grep $(date +%Y-%m-%d)

# Force a sync to cloud right now
rclone sync ~/recordings dropbox:TheTicket --progress

# Check disk usage
df -h ~
du -sh ~/recordings/
```

---

## 10. Easy SSH Access with Tailscale (Optional)

Tailscale gives you a stable hostname without managing ports or firewalls — useful if your VPS IP changes or you're behind NAT.

```bash
# On the VPS
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# On your Mac
# Install Tailscale app → your VPS appears as e.g. "my-vps"
ssh ticket@my-vps
```

---

## 11. Monitoring

The existing `monitor_health.py` works on Linux but macOS notifications won't fire. For email/push alerts on a VPS, add a simple health check cron:

```cron
*/15 * * * * python3 /home/ticket/ticket-recorder/monitor_health.py \
    --output-dir /home/ticket/recordings 2>&1 | \
    grep -i "error\|warn\|stall" | \
    mail -s "Ticket Recorder Alert" you@example.com
```

Or use a free uptime monitor like **UptimeRobot** to ping your VPS and alert you if it goes down.

---

## 12. Migrating from Mac

Once the VPS is recording and syncing reliably:

1. Confirm recordings appear in your Dropbox/Drive folder
2. On your Mac: `launchctl unload ~/Library/LaunchAgents/com.theticket.recorder.plist`
3. Leave the other Mac services (cleanup, monitor) running or unload them too
4. Your Mac Dropbox client will now receive files passively instead of producing them

---

## Cost Estimate

| Item | Cost |
|---|---|
| Hetzner CX23 VPS | ~€4/mo |
| Dropbox Plus (2 TB) | $9.99/mo (existing) |
| Google Drive 200 GB (alternative) | $2.99/mo |
| Bandwidth | Free (included with VPS) |

Total with Google Drive (fresh start, 30-day retention): **~$7/mo**
