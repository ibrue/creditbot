# Hosting Everything on One Windows 10/11 PC

The simplest setup: one PC runs the Discord bot, the kiosk API, **and**
the check-in kiosk GUI. No Docker, no NAS needed. (Prefer the NAS? See
[NAS_SETUP.md](NAS_SETUP.md) — the kiosk works with either.)

## 1. Install Python

Install Python 3.10+ from [python.org/downloads](https://www.python.org/downloads/).
In the installer, **check "Add python.exe to PATH"**.

## 2. Get the project

Either [download the ZIP](https://github.com/ibrue/creditbot/archive/refs/heads/main.zip)
and extract it (e.g. to `C:\creditbot`), or:

```
git clone https://github.com/ibrue/creditbot.git
```

## 3. Run it

Double-click **`start_server.bat`** — that's it. On first run it installs
everything itself (virtual environment, dependencies) and opens `.env` in
Notepad. Fill in:

- `DISCORD_TOKEN` — your bot token
- `CHECKIN_CHANNEL_ID` / `ANNOUNCEMENTS_CHANNEL_ID`
- `KIOSK_API_KEY` — any long random string (the kiosk uses the same one)

**Already have a database?** Keep `social_credit.db` in the project
folder — it's picked up automatically.

One console window runs both the Discord bot and the kiosk API
(port 8765). Leave it open; logs show check-ins as they happen, and if
the server ever crashes it restarts itself after 5 seconds (close the
window to actually stop it).

## 4. Set up the kiosk GUI (same PC)

Double-click **`kiosk\start_kiosk.bat`** — it also installs its own
dependencies and downloads the face models on first run, then opens the
kiosk `.env`; use:

```
KIOSK_API_URL=http://localhost:8765
KIOSK_API_KEY=<same key as step 3>
```

Save, close Notepad, and the kiosk launches (it also auto-restarts if it
ever crashes).

## Optional: fun AI captions for check-in photos

When someone checks in at the kiosk, the bot posts their photo to the
check-in channel. A local LLM (Ollama) can add a playful,
school-friendly caption to each photo — everything runs on this PC, no
cloud.

Double-click **`setup_ollama.bat`**. It installs Ollama (via winget, or
the official installer as a fallback), picks a vision model that fits
this PC's RAM (`llava`, or the smaller `moondream` under 12 GB),
downloads it, and sets `OLLAMA_MODEL` in `.env`. Then restart
`start_server.bat`.

No Ollama? No problem — photos post without captions. Turn photo posting
off entirely with `KIOSK_POST_PHOTOS=0` in `.env`.

## Where your data lives (and moving it to Documents)

By default everything stays in the project folder: `social_credit.db`
(the database), `kiosk_uploads\` (photos waiting to post — deleted after
posting), `kiosk\face_log\` (face captures), `kiosk\models\` (the face
models), and the two `.env` files. Ollama keeps its AI models in
`C:\Users\<you>\.ollama`.

To keep the data in your Documents folder instead, set these in the
`.env` files (`%USERPROFILE%` expands to `C:\Users\<you>`, and the
folders are created automatically):

In the project's `.env`:
```
DATABASE_PATH=%USERPROFILE%\Documents\CreditBot\social_credit.db
KIOSK_UPLOADS_DIR=%USERPROFILE%\Documents\CreditBot\checkin_photos
```

In `kiosk\.env`:
```
KIOSK_FACE_LOG_DIR=%USERPROFILE%\Documents\CreditBot\faces
```

If you already have data, move the existing `social_credit.db` into the
new folder before restarting. Updates never touch any of these files
either way.

## Automatic updates from GitHub

If you installed with `git clone` (recommended — install
[Git for Windows](https://git-scm.com/download/win) if needed), the
server and the kiosk **update themselves**: every 30 minutes they check
the repo's `main` branch on GitHub, pull merged changes, reinstall
dependencies if they changed, and restart on the new code (the kiosk
waits until nobody is mid-check-in). So shipping a change to the lab PC
is just: merge the pull request on GitHub.

- Your `.env`, database, face models, and face logs are never touched by
  updates.
- If someone hand-edited code on the PC, the update safely refuses
  instead of overwriting — the console log says so.
- Turn it off with `AUTO_UPDATE=0`, follow a different branch with
  `UPDATE_BRANCH=...`, or update manually anytime with `python updater.py`.
- ZIP installs can't auto-update (no git history) — the console says so
  at startup.

## Auto-start on boot

Press `Win+R`, type `shell:startup`, Enter — then put shortcuts to
**both** `start_server.bat` and `kiosk\start_kiosk.bat` in that folder.
With Windows auto-login enabled, the PC boots straight into a working
kiosk.

## Notes

- The PC needs to stay on for the Discord bot to work (disable sleep:
  Settings → System → Power → "When plugged in, put my device to sleep" →
  Never).
- If another kiosk machine on the LAN should reach the API, allow port
  8765 through Windows Defender Firewall (it will prompt on first run).
  Keep it LAN-only — don't port-forward it on your router.
