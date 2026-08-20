"""Run the Discord bot and the kiosk API together in one process.

Handy when hosting everything on one machine (e.g. the Windows 10 PC
that also runs the kiosk):  python start_all.py
"""
import threading

import uvicorn

import bot
import updater


def run_api():
    uvicorn.run("api:app", host="0.0.0.0", port=8765, log_level="info")


def main():
    # Pull merged changes from GitHub automatically and restart
    updater.start_background_updater("server")

    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    print("Kiosk API starting on http://0.0.0.0:8765")
    bot.main()  # blocks until the bot stops; API thread dies with it


if __name__ == "__main__":
    main()
