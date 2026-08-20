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
check-in channel. If you install [Ollama](https://ollama.com/download)
on this PC and pull a vision model, a local LLM writes a playful,
school-friendly caption for each photo (everything runs on this PC —
no cloud):

```
ollama pull llava
```

On a slower PC use the much smaller model and set it in `.env`:

```
ollama pull moondream
```
```
OLLAMA_MODEL=moondream
```

No Ollama? No problem — photos post without captions. Turn photo posting
off entirely with `KIOSK_POST_PHOTOS=0` in `.env`.

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
