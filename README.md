# Robotics Social Credit System

A Discord bot and a face-recognition check-in terminal for a robotics lab.
Members earn "social credit" for lab time and for helping each other, and a
computer by the door recognises them and checks them in without anyone typing
anything.

Everything runs on your own hardware. Face recognition happens locally — the
server stores 128-number embeddings, not photographs of people's faces.

## What you get

- **A Discord bot** — slash commands, a daily check-in post, a weekly
  leaderboard with awards, and automatic check-out for people who forget.
- **A walk-up terminal** at `http://<server>:8765/app` — press *Check in*, look
  at the camera, and it credits *you*. Returns to idle for the next person.
- **Self-registration** at `/app/enroll` — a newcomer types their name, picks
  their Discord account, and looks at the camera. No password, no admin needed.
- **A lab timeline** on the terminal showing who came and went, with the photo
  the camera took.
- **A browser admin page** at `/app/admin` — connect Discord by pasting a token
  and picking your server and channels from dropdowns. No config files.
- **A diagnostics page** at `/app/admin/diagnostics` — which camera is in use,
  whether the models loaded, who is enrolled, and a recognition test that
  reports the actual match score.

## Quick start

You need: a machine that runs Docker (a NAS, a spare PC, a Raspberry Pi), and a
Discord account. About fifteen minutes.

### 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   → **New Application**.
2. **Bot** → **Reset Token** → copy it. You will paste it into a web page in a
   moment; it never needs to go into a file.
3. On the same page enable both privileged intents:
   **MESSAGE CONTENT INTENT** and **SERVER MEMBERS INTENT**.
   The second is what lets people pick their own Discord account when registering.
4. **OAuth2 → URL Generator** → scopes `bot` and `applications.commands`, then
   permissions: Send Messages, Add Reactions, Read Message History, Embed Links,
   Attach Files. Open the generated URL and invite the bot to your server.

### 2. Start the server

```bash
git clone https://github.com/ibrue/creditbot.git
cd creditbot
cp .env.example .env
```

Set just two things in `.env` — everything else can wait:

```env
WEB_PASSWORD=           # the shared lab password, for the terminal
KIOSK_API_KEY=          # any long random string, only if you use the physical kiosk
```

Generate a decent password with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(18))"
```

Then:

```bash
docker compose up -d --build
```

Two containers start: `creditbot` (the Discord bot) and `creditbot-api` (the
terminal and its API, on port 8765). Check with `curl http://localhost:8765/health`.

*No Docker?* `pip install -r requirements.txt` then `python start_all.py` runs
both in one process.

### 3. Connect Discord from your browser

Open **`http://<server>:8765/app/admin`** and sign in with `WEB_ADMIN_PASSWORD`
(or `WEB_PASSWORD` if you have not set a separate one).

Paste the bot token → **Test** → pick your server → pick your channels → **Save**.

That writes to `data/settings_overrides.json`, which overrides `.env`, so you
never have to hunt for channel IDs by hand. The API applies it immediately; the
bot picks it up on `docker compose restart bot`.

### 4. Set up the terminal

Open **`http://<server>:8765/app`** on the computer by the door and sign in once
with the lab password. It stays armed; there is no per-person login.

**The camera needs HTTPS.** Browsers only allow camera access on `https://` or
`localhost`, so a plain `http://<server-ip>:8765` will not see a webcam. The
easiest fix is Tailscale (below). On the server itself, `http://localhost:8765`
works fine.

If the machine has more than one camera, choose which one at
`/app/admin/diagnostics`. The choice is remembered per machine.

### 5. Get people enrolled

Send everyone to **`http://<server>:8765/app/enroll`** on the terminal. They type
their name, pick their Discord account, and look at the camera for five frames.

That last part matters: picking the Discord account is what makes credits earned
at the terminal and credits earned in Discord belong to the same person.

## Reaching it from outside the lab

**Do not port-forward 8765.** Use [Tailscale](https://tailscale.com), which also
gives you the HTTPS the camera needs:

```bash
tailscale funnel --bg --https=443 localhost:8765
```

That publishes `https://<machine>.<tailnet>.ts.net` with a real certificate and
no open router ports. Then set `WEB_HTTPS=1` and `WEB_TRUST_PROXY=1` in `.env`
so session cookies are marked Secure and the login rate limiter sees real client
addresses.

Be aware of what this means: the site is then reachable by anyone who finds the
URL, protected by one shared password. Choose a long one, and set a **separate**
`WEB_ADMIN_PASSWORD` — the admin page holds your bot token and a live server log.
Both can be changed later from `/app/admin` without touching the server, so
rotating the key when somebody leaves the team is a browser job.
Self-registration is automatically refused for traffic arriving over Funnel, so
strangers cannot enrol their face under someone else's name; set
`WEB_ENROLL_PUBLIC=1` only if you actually want that.

## Commands

| Command | Description |
|---------|-------------|
| `/credit [@user]` | Check social credit score |
| `/stats [@user]` | View detailed statistics |
| `/leaderboard` | Show weekly leaderboard |
| `/alltime` | Show all-time leaderboard |
| `/history` | View your recent transactions |
| `/thank @user [reason]` | Thank someone for helping (+5 to them) |
| `/documented [description]` | Log documentation work (+3) |
| `/magic-smoke @user` | Vote that someone released magic smoke |

## Credit system

### Earning credits

| Activity | Credits |
|----------|---------|
| Lab time | +1 per 30 min |
| First to check in | +3 |
| Night owl (after 8 PM) | +2 |
| Weekend warrior | +5 |
| Daily streak | +2 per day |
| Being thanked | +5 |
| Documentation | +3 |
| Meme post (1x/day) | +1 |

### Losing credits

| Activity | Credits |
|----------|---------|
| Magic smoke (3+ votes) | -10 |
| Forgot to check out | Session voided (0 earned) |
| Got roasted (5+ 🔥) | -1 |

All of it is in `config.py` — credit values, the night-owl hour, the auto-checkout
window, and when the daily and weekly posts go out.

### Weekly awards

Every Sunday at 6 PM:
- 🏆 **Supreme Leader** — highest credits
- 🥈 **Comrade of the People** — second place
- 🥉 **Rising Star** — third place
- ⏰ **Lab Rat** — most lab hours
- 📈 **Most Improved** — biggest jump from last week

## Settings

Only `WEB_PASSWORD` is really required. Discord settings are better done through
`/app/admin`, which overrides anything here.

| Setting | Default | What it does |
|---|---|---|
| `WEB_PASSWORD` | *(unset)* | Shared lab password. Unset means nobody can sign in. Rotate it from `/app/admin`. |
| `WEB_ADMIN_PASSWORD` | *(unset)* | Password for `/app/admin`. Unset means the lab password opens it too — set this if the site is public. |
| `WEB_ENABLED` | `1` | `0` serves no web client; the kiosk API keeps working. |
| `WEB_HTTPS` | `0` | `1` when reached over HTTPS, so cookies are marked Secure. |
| `WEB_TRUST_PROXY` | `0` | `1` only behind a proxy you control that sets `X-Forwarded-For`. |
| `WEB_ENROLL_PUBLIC` | `0` | `1` allows self-registration from the public internet. |
| `KIOSK_API_KEY` | *(unset)* | Required by the physical kiosk app; unset rejects it. |
| `KIOSK_CAMERA` | *(unset)* | Preferred webcam, as part of its name (e.g. `BRIO`). Each terminal can override it. |
| `KIOSK_POST_PHOTOS` | `1` | `0` stops check-in photos being posted to Discord. |
| `OLLAMA_ENABLED` | `1` | Local-LLM captions on check-in photos. `0` if you have no Ollama. |
| `DATABASE_PATH` | `social_credit.db` | Docker overrides this to `/data/social_credit.db`. |
| `TZ` | `America/Chicago` | Bonuses like night-owl use local time. |
| `AUTO_UPDATE` | `1` | Pull merged changes from GitHub and restart (needs a git clone). |

## The physical kiosk (optional)

`kiosk/` holds a fullscreen Tkinter app for a dedicated Windows or Linux machine
with a webcam — the same face models, but native rather than in a browser, so it
needs no HTTPS. See [kiosk/README.md](kiosk/README.md). Point it at the server
with `KIOSK_API_URL` and the same `KIOSK_API_KEY`.

The browser terminal at `/app` does the same job and is easier to set up; the
native kiosk is worth it for a permanently mounted appliance.

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite runs offline against a throwaway database — it never touches your real
`social_credit.db`, and it stubs out Discord, the face models, and Ollama, so no
token or webcam is needed. It also runs on every pull request across Linux and
Windows on Python 3.10–3.12 (`.github/workflows/tests.yml`).

On Windows you can double-click `run_tests.bat`, which installs what it needs.

## Other ways to run it

- **UGREEN NAS or any Docker host** — [NAS_SETUP.md](NAS_SETUP.md), including an
  updater that deploys straight from GitHub without git installed on the host.
- **One Windows PC** — [WINDOWS_SETUP.md](WINDOWS_SETUP.md); double-click
  `START.bat` and it installs whatever is missing.
- **Automatic updates** — a clone following `main` pulls merged changes and
  restarts within ~30 minutes. `AUTO_UPDATE=0` to opt out.

## Privacy

Enrolment is opt-in and per-person. The server stores face *embeddings* — 128
numbers — not images. Check-in photos are kept only long enough for the bot to
post them, plus a small thumbnail for the terminal's timeline, capped to the most
recent events. Anyone can be removed completely, which deletes their embeddings
with them.

## License

MIT — do whatever you want with it.
