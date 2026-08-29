# Robotics Social Credit System

A fun Discord bot for tracking "social credit" in your robotics lab/team. Members earn points through lab time and positive community behaviors.

## Features

- **Reaction-based check-ins**: React to daily messages to track lab time
- **Lab time credits**: Earn 1 credit per 30 minutes
- **Bonus credits**: First arrival, night owl, weekend warrior, streaks
- **Thank system**: `/thank @user` to give credits for helping
- **Weekly leaderboard**: Automatic Sunday announcements with awards
- **Fun penalties**: Magic smoke votes, roast reactions

## Quick Start

### 1. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to "Bot" section → Click "Add Bot"
4. Copy the **Bot Token** (you'll need this)
5. Enable these Privileged Gateway Intents:
   - MESSAGE CONTENT INTENT
   - SERVER MEMBERS INTENT

### 2. Invite the Bot to Your Server

1. Go to OAuth2 → URL Generator
2. Select scopes: `bot`, `applications.commands`
3. Select permissions:
   - Send Messages
   - Add Reactions
   - Read Message History
   - Use Slash Commands
   - Embed Links
4. Copy the generated URL and open it to invite the bot

### 3. Get Channel IDs

1. In Discord, go to User Settings → App Settings → Advanced
2. Enable "Developer Mode"
3. Right-click your check-ins channel → "Copy ID"

### 4. Configure the Bot

Create a `.env` file in the project folder:

```env
DISCORD_TOKEN=your-bot-token-here
CHECKIN_CHANNEL_ID=123456789012345678
ANNOUNCEMENTS_CHANNEL_ID=123456789012345678
MEMES_CHANNEL_ID=123456789012345678
```

Or edit `config.py` directly.

### 5. Install and Run

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python bot.py
```

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

## Credit System

### Earning Credits

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

### Losing Credits

| Activity | Credits |
|----------|---------|
| Magic smoke (3+ votes) | -10 |
| Forgot to check out | Session voided (0 earned) |
| Got roasted (5+ 🔥) | -1 |

## Weekly Awards

Every Sunday at 6 PM:
- 🏆 **Supreme Leader** - Highest credits
- 🥈 **Comrade of the People** - Second place
- 🥉 **Rising Star** - Third place
- ⏰ **Lab Rat** - Most lab hours
- 📈 **Most Improved** - Biggest jump from last week

## Self-Hosting + Facial-Recognition Kiosk

- **One Windows 10/11 PC (easiest):** see [WINDOWS_SETUP.md](WINDOWS_SETUP.md) —
  double-click **`START.bat`** and it runs the bot, the kiosk API and the
  check-in kiosk together, installing whatever is missing on the first run.
- **UGREEN NAS (or any Docker host):** see [NAS_SETUP.md](NAS_SETUP.md) —
  `docker compose up -d --build` runs the bot plus the kiosk API on
  port 8765 sharing the same database.
- **Check in from a browser:** the same server hosts a web client at
  `http://<server>:8765/app` — sign in with a shared lab password, then
  face login at the webcam (same models and enrollments as the kiosk) or
  pick your name, from any computer or phone.
- **Browser kiosk:** the lab's shared computer uses
  `http://<server>:8765/app/kiosk` — the password arms it once, then each
  press of Check in / Check out recognizes whoever is standing there and
  credits *them*. Newcomers register themselves at `/app/enroll`, no
  password needed. (`/app/station`, which credited one shared account for
  everybody, is retired and redirects here.) Reach any of it from outside
  the lab over Tailscale rather than port-forwarding.
- **Admin from a browser:** `http://<server>:8765/app/admin` hooks the
  system up to your Discord server with a GUI — paste the bot token, test
  it, pick the server and channels from dropdowns — and shows a live
  terminal of what the server is doing.
- **Check-in kiosk:** see [kiosk/README.md](kiosk/README.md) — a GUI for
  Windows 10/11 or Linux with big Check In / Check Out buttons that
  recognizes enrolled members' faces via webcam and checks them in with
  the same credits and bonuses. Recognized captures are logged locally and
  the kiosk retunes each member's samples from them automatically, so
  recognition improves the more the lab uses it.
- **Automatic updates:** machines installed via `git clone` follow the
  `main` branch — merge a PR on GitHub and the server and kiosk pull it
  and restart themselves within ~30 minutes (`AUTO_UPDATE=0` to opt out,
  `python updater.py` to update on demand).

## Cloud Deployment

### Railway (Recommended)

1. Push your code to GitHub
2. Go to [railway.app](https://railway.app)
3. New Project → Deploy from GitHub repo
4. Add environment variables in Railway dashboard
5. Deploy!

### Other Options

- **Fly.io**: `fly launch` then `fly deploy`
- **Render**: Connect GitHub, add env vars, deploy
- **Heroku**: Similar to Railway
- **VPS/Raspberry Pi**: Run with `screen` or `systemd`

## File Structure

```
robotics-social-credit/
├── bot.py              # Main entry point
├── config.py           # Configuration
├── database.py         # SQLite database
├── cogs/
│   ├── checkin.py      # Check-in reactions
│   ├── social_credit.py # Credit commands
│   └── leaderboard.py  # Stats & weekly posts
├── utils/
│   └── helpers.py      # Utility functions
├── requirements.txt
└── README.md
```

## Running the Tests

On Windows, double-click **`run_tests.bat`** — it installs what it needs on
first run. Anywhere else:

```bash
pip install -r requirements-dev.txt
pytest
```

The suite runs entirely offline against a throwaway SQLite database — it
never touches your real `social_credit.db`, and it stubs out Discord and
Ollama, so no bot token or local model is needed.

```
tests/test_helpers.py        formatting, tiers, streak messages
tests/test_database.py       credits, check-ins, streaks, votes, audits
tests/test_checkin_logic.py  kiosk check-in bonuses (matches Discord rules)
tests/test_api.py            kiosk HTTP API and its authentication
tests/test_caption.py        local-LLM captions and the safety filter
tests/test_kiosk_feed.py     posting kiosk photos to Discord, and retries
tests/test_updater.py        auto-update, and its never-clobber guarantees
```

They also run automatically on every pull request, on Linux and Windows
across Python 3.10-3.12 (`.github/workflows/tests.yml`).

## Customization

Edit `config.py` to change:
- Credit values for each activity
- Night owl hour (default: 8 PM)
- Auto-checkout time (default: 12 hours)
- Daily check-in time (default: 8 AM)
- Weekly announcement day/time

## License

MIT - Do whatever you want with it!
