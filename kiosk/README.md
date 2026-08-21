# Facial-Recognition Check-In Kiosk (Windows or Linux)

A fullscreen GUI for the lab: press **CHECK IN** or **CHECK OUT**,
look at the webcam, and the kiosk recognizes your face and checks you
in/out through the creditbot API — same credits, bonuses, and streaks as
checking in on Discord. The API can run on the same PC
([WINDOWS_SETUP.md](../WINDOWS_SETUP.md)) or on a NAS
([NAS_SETUP.md](../NAS_SETUP.md)).

Face recognition runs entirely on the kiosk machine using OpenCV's
YuNet (detection) + SFace (recognition) ONNX models — CPU-only, no GPU
needed. Any Windows 10/11 PC or Linux box (Raspberry Pi included) with a
webcam works.

**Privacy:** enrollment is opt-in — each member enrolls their own face at
the kiosk. The server database stores only compact face *embeddings*
(128 numbers), not photos. The kiosk machine additionally keeps a local
capture log of enrolled members' faces (for fine-tuning, below) — it
never saves faces of people who aren't enrolled, and it stays on the
kiosk machine. Anyone can be removed completely (see below).

## Setup on Windows 10/11

1. Install Python 3.10+ from [python.org/downloads](https://www.python.org/downloads/)
   — in the installer, **check "Add python.exe to PATH"**. (Tkinter and
   everything else the GUI needs is included by default.)
2. Get this repo onto the PC — either
   [download the ZIP](https://github.com/ibrue/creditbot/archive/refs/heads/main.zip)
   and extract it, or `git clone https://github.com/ibrue/creditbot.git`.
3. Open the `creditbot\kiosk` folder and double-click **`start_kiosk.bat`**.
   On first run it installs everything itself — virtual environment,
   dependencies, the two face models (~40 MB) — and opens `.env` in
   Notepad; fill in:
   - `KIOSK_API_URL=` — `http://localhost:8765` if the same PC runs the
     server, or `http://<server-ip>:8765`
   - `KIOSK_API_KEY=` — the same key as in the server's `.env`

   Save, close Notepad, and the kiosk starts. It auto-restarts itself if
   it ever crashes (close the console window to stop it).

To auto-start on boot: press `Win+R`, type `shell:startup`, Enter, and put
a shortcut to `start_kiosk.bat` in that folder (pair with Windows
auto-login for a true appliance setup).

## Setup on Linux

Requires Python 3.10+, a webcam, and a desktop session (X11/Wayland).

```bash
sudo apt install python3-tk python3-venv   # Debian/Ubuntu/Raspberry Pi OS
git clone https://github.com/ibrue/creditbot.git
cd creditbot/kiosk
./run_kiosk.sh
```

`run_kiosk.sh` installs its own dependencies and models on first run,
creates `.env` for you to fill in (KIOSK_API_URL, KIOSK_API_KEY), and
auto-restarts the kiosk if it crashes. For auto-start on boot, see
`creditbot-kiosk.service` (systemd user service).

## Using it

Keys: **F11** fullscreen, **Esc** windowed, **Ctrl+Q** quit.

1. **Add a person (once):** press **👤 Add Person / Enroll**. Search your
   Discord server by name — the kiosk pulls the matching accounts straight
   from Discord (needs `GUILD_ID` in the server `.env`) — or pick someone
   already in the system, or type a Discord ID. Then **➕ Add Person**
   just links them, or **📷 Add + Enroll Face** also captures their face:
   the kiosk guides them to look straight, then turn to each side, so
   recognition works from multiple angles.
2. **Check in:** press **CHECK IN** and look at the camera. After it
   recognizes you, it asks you to slowly turn your head side to side — a
   quick liveness check so a photo of someone can't check them in. Then
   it checks you in, shows your bonuses, and (if enabled) sends your
   check-in photo to the Discord check-in channel, where the bot posts it
   with a fun AI caption if the server has Ollama running.
3. **Check out:** press **CHECK OUT**, look at the camera, turn your head
   when asked. Shows your session time and credits earned.

If someone isn't recognized (haircut, glasses, lighting), just enroll
them again — extra samples improve matching. To skip the head-turn
challenge (e.g. for a demo), set `KIOSK_LIVENESS=0` in `.env`; to keep
check-in photos off Discord, set `KIOSK_SEND_PHOTO=0`.

## Face capture log + fine-tuning recognition

Every recognized check-in/check-out (and every enrollment sample) saves
the face crop locally under `face_log/<discord_id>_<name>/`, capped at
500 captures per person (oldest are pruned). Toggle with
`KIOSK_FACE_LOG=0` or move the folder with `KIOSK_FACE_LOG_DIR` in
`.env`.

### It fine-tunes itself

The kiosk rebuilds each person's stored embeddings from those captures on
its own, keeping the most *diverse* set (different angles, lighting,
glasses on/off) — which beats the handful of same-pose samples from
enrollment day. So recognition gets better the more the lab uses it, with
nothing to run.

It only fires while the kiosk is idle, never mid-check-in, and only for
people who have actually gathered new captures since their last retune
(a first pass after ~12 captures, then roughly every 25 new ones).

| Setting (`.env`) | Default | What it does |
|---|---|---|
| `KIOSK_AUTO_RETUNE` | `1` | Set to `0` to turn the automatic pass off |
| `KIOSK_AUTO_RETUNE_INTERVAL_MIN` | `360` | How often to look for people who are due |

**In a hurry?** Press **✨ Improve Recognition** on the kiosk to retune
everyone right now; it refreshes the faces itself when it finishes.

If someone still isn't recognized well, have them use the kiosk normally
for a few days so the log gathers more varied captures.

<details>
<summary>Running it by hand (troubleshooting)</summary>

```bash
python retune_faces.py            # everyone who is due
python retune_faces.py --all      # everyone, due or not
python retune_faces.py --person <discord-id>   # just one person
python retune_faces.py --dry-run  # preview without changing anything
```

On Windows run it from the kiosk folder with
`.venv\Scripts\python retune_faces.py`, then press **🔄 Refresh Faces**.
</details>

## Removing someone's face data

```bash
curl -X DELETE -H "X-API-Key: <your-key>" http://<server-ip>:8765/faces/<discord-id>
```

(Windows 10+ ships `curl` in Command Prompt / PowerShell.) Then delete
their `face_log/<discord_id>_<name>` folder on the kiosk machine to
remove the local captures too.

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
