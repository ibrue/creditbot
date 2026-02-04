#!/usr/bin/env python3
"""
Simple cross-platform bot runner.
Works on Windows, macOS, and Linux.

Usage:
    python start_bot.py

The bot will run continuously until you press Ctrl+C to stop it.
"""

import subprocess
import sys
import os
import signal
import time

def main():
    # Change to the script's directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    print("=" * 50)
    print("Social Credit Bot Launcher")
    print("=" * 50)
    print(f"Working directory: {script_dir}")
    print("Press Ctrl+C to stop the bot")
    print("=" * 50)
    print()

    # Check for .env file
    if not os.path.exists(".env"):
        print("ERROR: No .env file found!")
        print("Copy .env.example to .env and fill in your values:")
        print("  cp .env.example .env")
        print("Then edit .env with your Discord token and channel IDs.")
        sys.exit(1)

    # Check for requirements
    try:
        import discord
        import dotenv
        import apscheduler
    except ImportError:
        print("Installing dependencies...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    # Run the bot with auto-restart on crash
    restart_count = 0
    max_restarts = 5

    while restart_count < max_restarts:
        print(f"\nStarting bot... (attempt {restart_count + 1})")

        try:
            # Run bot.py using the same Python interpreter
            process = subprocess.Popen(
                [sys.executable, "bot.py"],
                cwd=script_dir
            )

            # Wait for the process to complete
            process.wait()

            exit_code = process.returncode

            if exit_code == 0:
                print("\nBot exited normally.")
                break
            else:
                print(f"\nBot crashed with exit code {exit_code}")
                restart_count += 1
                if restart_count < max_restarts:
                    print(f"Restarting in 10 seconds... ({max_restarts - restart_count} attempts remaining)")
                    time.sleep(10)

        except KeyboardInterrupt:
            print("\n\nShutting down bot...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            print("Bot stopped.")
            break

    if restart_count >= max_restarts:
        print(f"\nBot crashed {max_restarts} times. Check your configuration and logs.")
        sys.exit(1)

if __name__ == "__main__":
    main()
