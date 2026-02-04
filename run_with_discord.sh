#!/bin/bash
# Run the social credit bot only when Discord is open
# This script checks every 60 seconds if Discord is running

BOT_DIR="/Users/ibrue/robotics-social-credit"
cd "$BOT_DIR"

# Load environment variables from .env file
if [ -f "$BOT_DIR/.env" ]; then
    export $(grep -v '^#' "$BOT_DIR/.env" | xargs)
fi

# Track consecutive failures to avoid spam restarts
FAIL_COUNT=0
MAX_FAILURES=3

while true; do
    # Check if Discord is running
    if pgrep -x "Discord" > /dev/null; then
        # Discord is running, start bot if not already running
        if ! pgrep -f "python3.*bot.py" > /dev/null; then
            # Check if we've failed too many times
            if [ $FAIL_COUNT -ge $MAX_FAILURES ]; then
                echo "$(date): Bot failed $FAIL_COUNT times, waiting 5 minutes before retry..."
                sleep 300
                FAIL_COUNT=0
            fi

            echo "$(date): Discord detected, starting bot..."
            cd "$BOT_DIR"
            /usr/bin/python3 bot.py &
            BOT_PID=$!

            # Wait a bit and check if it's still running
            sleep 10
            if ! kill -0 $BOT_PID 2>/dev/null; then
                echo "$(date): Bot exited quickly, incrementing failure count"
                FAIL_COUNT=$((FAIL_COUNT + 1))
            else
                # Bot started successfully, reset fail count
                FAIL_COUNT=0
            fi
        fi
    else
        # Discord is not running, stop bot if running
        if pgrep -f "python3.*bot.py" > /dev/null; then
            echo "$(date): Discord closed, stopping bot..."
            pkill -f "python3.*bot.py"
        fi
        # Reset fail count when Discord closes
        FAIL_COUNT=0
    fi
    sleep 60
done
