# Facial-Recognition Check-In Kiosk (Windows or Linux)

A fullscreen GUI for the lab: press **CHECK IN** or **CHECK OUT**,
look at the webcam, and the kiosk recognizes your face and checks you
in/out through the creditbot API on the NAS — same credits, bonuses, and
streaks as checking in on Discord.

Face recognition runs entirely on the kiosk machine using OpenCV's
YuNet (detection) + SFace (recognition) ONNX models — CPU-only, no GPU
needed. Any Windows 10/11 PC or Linux box (Raspberry Pi included) with a
webcam works.

**Privacy:** enrollment is opt-in — each member enrolls their own face at
the kiosk. Only compact face *embeddings* (128 numbers) are stored, not
photos, and they're kept in the bot's database on your NAS. Anyone can be
removed with one API call (see below).

## Setup on Windows 10/11

1. Install Python 3.10+ from [python.org/downloads](https://www.python.org/downloads/)
   — in the installer, **check "Add python.exe to PATH"**. (Tkinter and
   everything else the GUI needs is included by default.)
2. Get this repo onto the PC — either
   [download the ZIP](https://github.com/ibrue/creditbot/archive/refs/heads/main.zip)
   and extract it, or `git clone https://github.com/ibrue/creditbot.git`.
3. Open the `creditbot\kiosk` folder and double-click **`setup_kiosk.bat`**.
   It creates a virtual environment, installs dependencies, downloads the
   two face models (~40 MB), and opens `.env` in Notepad — fill in:
   - `KIOSK_API_URL=http://<your-nas-ip>:8765`
   - `KIOSK_API_KEY=` the same key as in the `.env` on the NAS
4. Double-click **`start_kiosk.bat`** to run it.

To auto-start on boot: press `Win+R`, type `shell:startup`, Enter, and put
a shortcut to `start_kiosk.bat` in that folder (pair with Windows
auto-login for a true appliance setup).

## Setup on Linux

Requires Python 3.10+, a webcam, and a desktop session (X11/Wayland).

```bash
git clone https://github.com/ibrue/creditbot.git
cd creditbot/kiosk

# Tkinter comes from the OS package manager:
sudo apt install python3-tk        # Debian/Ubuntu/Raspberry Pi OS

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python download_models.py          # fetches the two ONNX models (~40 MB)

cp .env.example .env
nano .env                          # set KIOSK_API_URL (NAS IP) and KIOSK_API_KEY

python kiosk.py
```

For auto-start on boot, see `creditbot-kiosk.service` (systemd user
service).

## Using it

Keys: **F11** fullscreen, **Esc** windowed, **Ctrl+Q** quit.

1. **Enroll (once per person):** press **📷 Enroll Face**, pick yourself
   from the member list (or type your Discord ID + name), then look at
   the camera while it captures 5 samples.
2. **Check in:** press **CHECK IN**, look at the camera. When it
   recognizes you it checks you in and shows your bonuses.
3. **Check out:** press **CHECK OUT**, look at the camera. Shows your
   session time and credits earned.

If someone isn't recognized (haircut, glasses, lighting), just enroll
them again — extra samples improve matching.

## Removing someone's face data

```bash
curl -X DELETE -H "X-API-Key: <your-key>" http://<nas-ip>:8765/faces/<discord-id>
```

(Windows 10+ ships `curl` in Command Prompt / PowerShell.)

## Troubleshooting

- **"Can't reach API"** — check `curl http://<nas-ip>:8765/health` from
  the kiosk machine, and that `KIOSK_API_KEY` matches the NAS `.env`.
- **Camera won't open** — another app (Zoom/Teams/OBS) may be holding it;
  otherwise try `KIOSK_CAMERA=1` (or 2) in `.env`. On Windows also check
  Settings → Privacy → Camera → "Let desktop apps access your camera".
  On Linux, list devices with `ls /dev/video*`.
- **Nothing happens on double-click (Windows)** — run
  `.venv\Scripts\python.exe kiosk.py` from a Command Prompt in the kiosk
  folder to see the error message.
- **Recognizes the wrong person / nobody** — enroll more samples per
  person, improve lighting, and stand ~0.5–1 m from the camera. The match
  threshold is `COSINE_THRESHOLD` in `face_engine.py` (raise it to be
  stricter).
