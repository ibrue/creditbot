#!/usr/bin/env bash
# Start the check-in kiosk on Linux. Installs everything it needs on
# first run and auto-restarts on crashes.
set -u
cd "$(dirname "$0")"

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "Tkinter is missing — install it first:  sudo apt install python3-tk"
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "First run — creating virtual environment..."
    python3 -m venv .venv || exit 1
fi

if ! .venv/bin/python -c "import cv2, numpy, PIL, requests, dotenv" >/dev/null 2>&1; then
    echo "Installing dependencies..."
    .venv/bin/python -m pip install --upgrade pip || exit 1
    .venv/bin/python -m pip install -r requirements.txt || exit 1
fi

.venv/bin/python download_models.py || exit 1

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env — edit it (KIOSK_API_URL, KIOSK_API_KEY) and re-run."
    exit 1
fi

while true; do
    .venv/bin/python kiosk.py
    code=$?
    if [ "$code" -eq 0 ]; then
        break  # clean quit (Ctrl+Q / window closed)
    fi
    echo "Kiosk crashed (exit $code). Restarting in 5 seconds — Ctrl+C to stop."
    sleep 5
done
