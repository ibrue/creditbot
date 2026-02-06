#!/bin/bash
# Start the Subway Surfers Pi kiosk
# Add to autostart on the Pi for boot:
#   /etc/xdg/lxsession/LXDE-pi/autostart -> @/home/pi/robotics-social-credit/start_kiosk.sh

export KIOSK_ENABLED=true
export KIOSK_FULLSCREEN=true
export DISPLAY=:0

BOT_DIR="$HOME/robotics-social-credit"
cd "$BOT_DIR"

# Load .env for Discord token and channel IDs
if [ -f "$BOT_DIR/.env" ]; then
    set -a
    source "$BOT_DIR/.env"
    set +a
fi

# Wait for X server (boot race condition)
while ! xdpyinfo -display :0 >/dev/null 2>&1; do
    echo "Waiting for X server..."
    sleep 2
done

python3 bot.py
