"""Shared check-in/check-out logic used by the kiosk API.

Mirrors the bonus rules in cogs/checkin.py (first arrival, night owl,
streak) so a kiosk check-in earns exactly the same credits as a
Discord check-in.
"""
from datetime import datetime

import config
import database
from utils.helpers import format_duration, is_night_owl_time


def perform_checkin(discord_id: str, username: str, source: str = "kiosk") -> dict:
    """Check a user in and apply bonuses. Returns a result dict."""
    existing = database.get_active_checkin(discord_id)
    if existing:
        checkin_time = datetime.fromisoformat(existing["checkin_time"])
        minutes = int((datetime.now() - checkin_time).total_seconds() / 60)
        return {
            "status": "already_checked_in",
            "checkin_time": existing["checkin_time"],
            "minutes_so_far": minutes,
        }

    is_first = database.is_first_checkin_today()
    database.start_checkin(discord_id, username, source)
    new_streak = database.update_streak(discord_id, username)

    bonuses = []

    if is_first:
        database.add_credits(
            discord_id, username,
            config.CREDITS["first_arrival"],
            "First arrival bonus"
        )
        bonuses.append(f"+{config.CREDITS['first_arrival']} First arrival!")

    if is_night_owl_time(config.NIGHT_OWL_HOUR):
        database.add_credits(
            discord_id, username,
            config.CREDITS["night_owl"],
            "Night owl bonus"
        )
        bonuses.append(f"+{config.CREDITS['night_owl']} Night owl!")

    if new_streak > 1 and new_streak <= 7:
        if database.can_earn_streak_bonus(discord_id):
            database.add_credits(
                discord_id, username,
                config.CREDITS["streak_bonus"],
                f"Streak bonus (day {new_streak})"
            )
            database.record_streak_bonus(discord_id)
            bonuses.append(f"+{config.CREDITS['streak_bonus']} Streak day {new_streak}!")
        else:
            bonuses.append(f"🔥 Streak day {new_streak}! (bonus already claimed today)")
    elif new_streak > 7:
        bonuses.append(f"🔥 Streak day {new_streak}! (bonus capped at 7 days)")

    return {
        "status": "checked_in",
        "checkin_time": datetime.now().isoformat(),
        "streak": new_streak,
        "bonuses": bonuses,
    }


def perform_checkout(discord_id: str) -> dict:
    """Check a user out. Returns a result dict."""
    result = database.end_checkin(discord_id)

    if not result:
        return {"status": "not_checked_in"}

    return {
        "status": "checked_out",
        "duration_minutes": result["duration_minutes"],
        "duration": format_duration(result["duration_minutes"]),
        "credits_earned": result["credits_earned"],
        "weekend_bonus": result["weekend_bonus"],
    }
