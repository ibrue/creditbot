#!/usr/bin/env python3
"""
Setup script for the Social Credit Bot.
Run this on a new computer to set up everything.

Usage:
    python setup.py
"""

import subprocess
import sys
import os
import shutil

def main():
    print("=" * 50)
    print("Social Credit Bot Setup")
    print("=" * 50)
    print()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Step 1: Check Python version
    print("[1/4] Checking Python version...")
    if sys.version_info < (3, 8):
        print(f"  ERROR: Python 3.8+ required. You have {sys.version}")
        sys.exit(1)
    print(f"  OK: Python {sys.version_info.major}.{sys.version_info.minor}")

    # Step 2: Install dependencies
    print("\n[2/4] Installing dependencies...")
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-r", "requirements.txt"
        ])
        print("  OK: Dependencies installed")
    except subprocess.CalledProcessError:
        print("  ERROR: Failed to install dependencies")
        sys.exit(1)

    # Step 3: Create .env file if it doesn't exist
    print("\n[3/4] Checking configuration...")
    env_file = os.path.join(script_dir, ".env")
    env_example = os.path.join(script_dir, ".env.example")

    if os.path.exists(env_file):
        print("  OK: .env file exists")
    else:
        if os.path.exists(env_example):
            shutil.copy(env_example, env_file)
            print("  Created .env from .env.example")
            print("  IMPORTANT: Edit .env with your Discord token and channel IDs!")
        else:
            print("  ERROR: No .env.example found")
            sys.exit(1)

    # Step 4: Verify required settings
    print("\n[4/4] Verifying configuration...")
    with open(env_file, 'r') as f:
        content = f.read()

    needs_config = False
    if 'your-bot-token-here' in content or 'DISCORD_TOKEN=\n' in content:
        print("  WARNING: DISCORD_TOKEN not set!")
        needs_config = True

    if 'CHECKIN_CHANNEL_ID=0' in content:
        print("  WARNING: CHECKIN_CHANNEL_ID not set!")
        needs_config = True

    if needs_config:
        print("\n" + "=" * 50)
        print("SETUP INCOMPLETE")
        print("=" * 50)
        print("\nEdit the .env file with your settings:")
        print(f"  {env_file}")
        print("\nYou need:")
        print("  1. Discord bot token from https://discord.com/developers/applications")
        print("  2. Channel IDs (enable Developer Mode in Discord, right-click channels)")
        print("\nAfter editing .env, run:")
        print("  python start_bot.py")
    else:
        print("  OK: Configuration looks complete")
        print("\n" + "=" * 50)
        print("SETUP COMPLETE")
        print("=" * 50)
        print("\nTo start the bot, run:")
        print("  python start_bot.py")

if __name__ == "__main__":
    main()
