import json
import os
from dotenv import load_dotenv

load_dotenv()


def expand_path(path: str) -> str:
    """Expand ~ and environment variables (%USERPROFILE%, $HOME) in paths
    so .env entries like %USERPROFILE%\\Documents\\CreditBot work."""
    return os.path.expanduser(os.path.expandvars(path))


# ---------------------------------------------------------------------
# Settings saved from the web admin page (/app/admin) live next to the
# database — the one path both docker containers share — and win over
# .env, so Discord can be hooked up from a browser instead of a shell.
# ---------------------------------------------------------------------
SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(
        expand_path(os.getenv("DATABASE_PATH", "social_credit.db")))) or ".",
    "settings_overrides.json")


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_settings = load_settings()


def setting(key: str, default: str = "") -> str:
    value = _settings.get(key)
    if value is not None and str(value).strip() != "":
        return str(value).strip()
    return os.getenv(key, default)


def _int_setting(key: str) -> int:
    try:
        return int(setting(key, "0") or "0")
    except ValueError:
        return 0


def save_settings(updates: dict):
    """Persist admin-page settings and apply them to this process.

    The bot container reads the same file at startup, so a bot restart
    picks the new values up there too.
    """
    global _settings
    data = load_settings()
    for key, value in updates.items():
        if value is None:
            continue
        if str(value).strip() == "":
            data.pop(key, None)
        else:
            data[key] = str(value).strip()
    parent = os.path.dirname(SETTINGS_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(SETTINGS_PATH, 0o600)  # the bot token lives in here
    except OSError:
        pass
    _settings = data
    _apply_settings()


def _apply_settings():
    global DISCORD_TOKEN, GUILD_ID, CHECKIN_CHANNEL_ID
    global ANNOUNCEMENTS_CHANNEL_ID, MEMES_CHANNEL_ID, NOTEBOOKING_CHANNEL_ID
    global KIOSK_CAMERA
    DISCORD_TOKEN = setting("DISCORD_TOKEN", "your-bot-token-here")
    GUILD_ID = _int_setting("GUILD_ID")
    CHECKIN_CHANNEL_ID = _int_setting("CHECKIN_CHANNEL_ID")
    ANNOUNCEMENTS_CHANNEL_ID = _int_setting("ANNOUNCEMENTS_CHANNEL_ID")
    MEMES_CHANNEL_ID = _int_setting("MEMES_CHANNEL_ID")
    NOTEBOOKING_CHANNEL_ID = _int_setting("NOTEBOOKING_CHANNEL_ID")
    KIOSK_CAMERA = setting("KIOSK_CAMERA", "")


# Discord Bot Token - Get this from https://discord.com/developers/applications
DISCORD_TOKEN = setting("DISCORD_TOKEN", "your-bot-token-here")

# Guild ID - Right-click server -> Copy ID (for instant command sync)
GUILD_ID = _int_setting("GUILD_ID")

# Channel IDs - Right-click channel -> Copy ID (enable Developer Mode in Discord settings)
CHECKIN_CHANNEL_ID = _int_setting("CHECKIN_CHANNEL_ID")
ANNOUNCEMENTS_CHANNEL_ID = _int_setting("ANNOUNCEMENTS_CHANNEL_ID")
MEMES_CHANNEL_ID = _int_setting("MEMES_CHANNEL_ID")  # Optional
NOTEBOOKING_CHANNEL_ID = _int_setting("NOTEBOOKING_CHANNEL_ID")  # Auto documentation credits

# Which webcam a terminal should prefer, as a substring of the device label
# (e.g. "BRIO"). A machine with several cameras otherwise gets whichever one
# the browser picks, which on a wall-mounted kiosk is usually the built-in one
# pointing at the ceiling. A terminal can override this locally in the browser.
KIOSK_CAMERA = setting("KIOSK_CAMERA", "")

# Database
DATABASE_PATH = expand_path(os.getenv("DATABASE_PATH", "social_credit.db"))

# Kiosk photo posting
# When the kiosk sends a check-in photo, the bot posts it to the check-in
# channel. Set KIOSK_POST_PHOTOS=0 to disable posting.
KIOSK_POST_PHOTOS = os.getenv("KIOSK_POST_PHOTOS", "1") == "1"
KIOSK_UPLOADS_DIR = expand_path(os.getenv("KIOSK_UPLOADS_DIR", "kiosk_uploads"))

# Local LLM captions (optional) — a vision model via Ollama writes a fun,
# school-friendly caption for kiosk check-in photos. Install Ollama and
# `ollama pull llava` (or moondream for slower PCs). Set OLLAMA_ENABLED=0
# or leave Ollama uninstalled to skip captions (photos still post).
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "1") == "1"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llava")

# Credit Values
CREDITS = {
    # Lab time
    "lab_time_per_30_min": 1,

    # Bonuses
    "first_arrival": 3,
    "night_owl": 2,          # Check-in after 8 PM
    "weekend_warrior": 5,     # Lab time on weekend
    "streak_bonus": 2,        # Per consecutive day
    "helping_others": 5,      # /thank command
    "documentation": 3,       # /documented command
    "meme_post": 1,           # Post in memes channel (1x/day max)

    # Deductions
    "magic_smoke": -10,       # Voted by 3+ members
    # Forgetting to check out is not a penalty — the session simply
    # earns nothing (see _auto_checkout in cogs/checkin.py).
    "roasted": -1,            # 5+ fire reactions
}

# Timing
NIGHT_OWL_HOUR = 20  # 8 PM (24-hour format)
AUTO_CHECKOUT_HOURS = 12  # Hours before auto-checkout
DAILY_CHECKIN_HOUR = 17  # 5 PM - when daily checkin message posts
DAILY_LEADERBOARD_HOUR = 19  # 7 PM - daily leaderboard post
WEEKLY_ANNOUNCEMENT_DAY = "sunday"
WEEKLY_ANNOUNCEMENT_HOUR = 18  # 6 PM

# Reactions
CHECKIN_EMOJI = "✅"
CHECKOUT_EMOJI = "❌"
ROAST_EMOJI = "🔥"
ROAST_THRESHOLD = 5  # Number of fire reactions for -1 credit

# Notebook voting
NOTEBOOK_UPVOTE_EMOJI = "👍"
NOTEBOOK_DOWNVOTE_EMOJI = "👎"
NOTEBOOK_VOTES_REQUIRED = 3  # Net positive votes needed for points
NOTEBOOK_DOWNVOTES_REQUIRED = 3  # Net negative votes needed for penalty
NOTEBOOK_DOWNVOTE_PENALTY = -2  # Points lost when rejected

# Magic smoke voting
MAGIC_SMOKE_VOTES_REQUIRED = 3

# Winner Role
# Set this to your role ID, or leave as 0 to auto-create the role
WINNER_ROLE_ID = int(os.getenv("WINNER_ROLE_ID", "0"))
WINNER_ROLE_NAME = "Supreme Leader"
WINNER_ROLE_COLOR = 0xFFD700  # Gold color
