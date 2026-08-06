#!/bin/bash
# Restart ticket-recorder if recorder.log has gone stale during show hours.
# Show hours (UTC): weekdays 11:00-23:59, Sunday 14:00-17:00.
# A 4-hour stale log during those windows = recorder is stuck.

LOG=/home/ticket/ticket-recorder/logs/recorder.log
WLOG=/home/ticket/ticket-recorder/logs/watchdog.log
STALE_HOURS=4

if [ ! -f "$LOG" ]; then exit 0; fi

age_secs=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
age_hours=$(( age_secs / 3600 ))
hour_utc=$(date -u +%-H)   # 0-23, no leading zero
dow=$(date -u +%u)          # 1=Mon … 7=Sun

# Are we in a recording window?
in_window=0
if [ "$dow" -le 5 ] && [ "$hour_utc" -ge 11 ] && [ "$hour_utc" -le 23 ]; then
    in_window=1
elif [ "$dow" -eq 7 ] && [ "$hour_utc" -ge 14 ] && [ "$hour_utc" -le 17 ]; then
    in_window=1
fi

if [ "$age_hours" -ge "$STALE_HOURS" ] && [ "$in_window" -eq 1 ]; then
    echo "$(date -u): recorder.log stale ${age_hours}h during show window — restarting" >> "$WLOG"
    sudo systemctl restart ticket-recorder
fi
