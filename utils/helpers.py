from datetime import datetime, timedelta


def format_duration(minutes: int) -> str:
    """Format minutes into a human-readable duration."""
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if remaining_minutes > 0:
        parts.append(f"{remaining_minutes} minute{'s' if remaining_minutes != 1 else ''}")

    return " ".join(parts)


def format_credits(credits: int) -> str:
    """Format credits with sign and color hint."""
    if credits > 0:
        return f"+{credits}"
    return str(credits)


def get_rank_emoji(rank: int) -> str:
    """Get emoji for leaderboard rank."""
    emojis = {
        1: "🏆",
        2: "🥈",
        3: "🥉",
    }
    return emojis.get(rank, f"#{rank}")


def get_credit_tier(credits: int) -> tuple[str, str]:
    """Get tier name and emoji based on total credits."""
    tiers = [
        (1000, "Supreme Leader", "👑"),
        (500, "Comrade General", "⭐"),
        (250, "Party Member", "🎖️"),
        (100, "Loyal Citizen", "🏅"),
        (50, "Promising Worker", "📈"),
        (10, "New Recruit", "🌱"),
        (0, "Unranked", "❓"),
    ]

    for threshold, name, emoji in tiers:
        if credits >= threshold:
            return name, emoji

    return "Debt Collector", "💀"  # Negative credits


def get_streak_message(streak: int) -> str:
    """Get a message based on streak length."""
    if streak >= 30:
        return "🔥 LEGENDARY! One month streak!"
    elif streak >= 14:
        return "🌟 Two week warrior!"
    elif streak >= 7:
        return "⚡ One week strong!"
    elif streak >= 3:
        return "📈 Building momentum!"
    elif streak >= 1:
        return "✨ Keep it up!"
    return ""


def time_until_next(hour: int) -> timedelta:
    """Calculate time until the next occurrence of a specific hour."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    if now >= target:
        target += timedelta(days=1)

    return target - now


def is_weekend() -> bool:
    """Check if today is a weekend."""
    return datetime.now().weekday() >= 5


def is_night_owl_time(hour: int = 20) -> bool:
    """Check if it's after night owl hour."""
    return datetime.now().hour >= hour
