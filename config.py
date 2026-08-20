import os
from dotenv import load_dotenv

load_dotenv()

# Discord Bot Token - Get this from https://discord.com/developers/applications
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "your-bot-token-here")

# Guild ID - Right-click server -> Copy ID (for instant command sync)
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# Channel IDs - Right-click channel -> Copy ID (enable Developer Mode in Discord settings)
CHECKIN_CHANNEL_ID = int(os.getenv("CHECKIN_CHANNEL_ID", "0"))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv("ANNOUNCEMENTS_CHANNEL_ID", "0"))
MEMES_CHANNEL_ID = int(os.getenv("MEMES_CHANNEL_ID", "0"))  # Optional
NOTEBOOKING_CHANNEL_ID = int(os.getenv("NOTEBOOKING_CHANNEL_ID", "0"))  # Auto documentation credits

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "social_credit.db")

# Kiosk photo posting
# When the kiosk sends a check-in photo, the bot posts it to the check-in
# channel. Set KIOSK_POST_PHOTOS=0 to disable posting.
KIOSK_POST_PHOTOS = os.getenv("KIOSK_POST_PHOTOS", "1") == "1"
KIOSK_UPLOADS_DIR = os.getenv("KIOSK_UPLOADS_DIR", "kiosk_uploads")

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
    "forgot_checkout": -2,    # Auto checkout after 12 hours
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
