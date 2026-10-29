"""Context Bot settings, all from the environment (or .env).

Every name is prefixed CONTEXTBOT_ so it can share creditbot's .env file
without either bot picking up the other's Discord token or channels.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


def _ids(key: str) -> set[int]:
    """A comma-separated list of Discord IDs, ignoring blanks and junk."""
    ids = set()
    for part in os.getenv(key, "").replace(" ", "").split(","):
        if part.isdigit():
            ids.add(int(part))
    return ids


# Discord — a separate application from creditbot, invited to the EXP server
DISCORD_TOKEN = os.getenv("CONTEXTBOT_DISCORD_TOKEN", "").strip()

# The server to work in. 0 means "the one server the bot has been invited to";
# messages from any other server are ignored either way.
GUILD_ID = _int("CONTEXTBOT_GUILD_ID", 0)

# Who counts as the CEO: may run reports, see judgments, and DM the bot.
# Empty means the owner of the server.
OWNER_IDS = _ids("CONTEXTBOT_OWNER_IDS")

# What the company is called in prompts and reports
ORG_NAME = os.getenv("CONTEXTBOT_ORG_NAME", "").strip() or "EXP"

# Channels to read. Empty means every channel the bot can see.
CHANNEL_IDS = _ids("CONTEXTBOT_CHANNEL_IDS")

# Scheduled reports are DM'd to the CEO. Set a channel to post them there too
# — make it a private one, since the weekly report includes the scorecard.
REPORT_CHANNEL_ID = _int("CONTEXTBOT_REPORT_CHANNEL_ID", 0)

# Schedule, in the server's local time (TZ). An hour of -1 turns a report off.
DAILY_HOUR = _int("CONTEXTBOT_DAILY_HOUR", 8)
WEEKLY_DAY = (os.getenv("CONTEXTBOT_WEEKLY_DAY", "").strip() or "friday").lower()
WEEKLY_HOUR = _int("CONTEXTBOT_WEEKLY_HOUR", 17)

# Logged messages older than this are deleted
RETENTION_DAYS = max(7, _int("CONTEXTBOT_RETENTION_DAYS", 90))

DATABASE_PATH = os.path.expanduser(os.path.expandvars(
    os.getenv("CONTEXTBOT_DATABASE_PATH", "contextbot.db")))

# Claude. The SDK reads ANTHROPIC_API_KEY itself; it is checked here only so
# the bot can say what is missing instead of failing on the first report.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MODEL = os.getenv("CONTEXTBOT_MODEL", "").strip() or "claude-opus-5-5"
# How hard Claude thinks: low | medium | high | xhigh | max
EFFORT = os.getenv("CONTEXTBOT_EFFORT", "").strip() or "high"            # reports and judgments
CHAT_EFFORT = os.getenv("CONTEXTBOT_CHAT_EFFORT", "").strip() or "medium"  # DM conversation

# Upper bound on the transcript sent with each request (~4 characters a
# token). When a window holds more, the oldest messages are left out and the
# report says so.
MAX_CONTEXT_CHARS = max(20_000, _int("CONTEXTBOT_MAX_CONTEXT_CHARS", 400_000))
