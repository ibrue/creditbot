# Facial-Recognition Check-In Kiosk

A fullscreen Linux GUI for the lab: press **CHECK IN** or **CHECK OUT**,
look at the webcam, and the kiosk recognizes your face and checks you
in/out through the creditbot API on the NAS — same credits, bonuses, and
streaks as checking in on Discord.

Face recognition runs entirely on the kiosk machine using OpenCV's
YuNet (detection) + SFace (recognition) ONNX models — CPU-only, no GPU
or dlib needed. Works fine on a Raspberry Pi 4/5 or any old laptop.

**Privacy:** enrollment is opt-in — each member enrolls their own face at
the kiosk. Only compact face *embeddings* (128 numbers) are stored, not
photos, and they're kept in the bot's database on your NAS. Anyone can be
removed with one API call (see below).

## Setup (on the kiosk machine)

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
```

Run it:

```bash
python kiosk.py
```

Keys: **F11** fullscreen, **Esc** windowed, **Ctrl+Q** quit.

## Using it

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
curl -X DELETE -H "X-API-Key: <your-key>" \
  http://<nas-ip>:8765/faces/<discord-id>
```

## Auto-start on boot

See `creditbot-kiosk.service` for a systemd user service that launches
the kiosk when the kiosk machine logs into its desktop (pair it with
auto-login for a true appliance setup).

## Troubleshooting

- **"Can't reach API"** — check `curl http://<nas-ip>:8765/health` from
  the kiosk machine, and that `KIOSK_API_KEY` matches the NAS `.env`.
- **Camera won't open** — try `KIOSK_CAMERA=1` (or 2). List devices with
  `ls /dev/video*`.
- **Recognizes the wrong person / nobody** — enroll more samples per
  person, improve lighting, and stand ~0.5–1 m from the camera. The match
  threshold is `COSINE_THRESHOLD` in `face_engine.py` (raise it to be
  stricter).
