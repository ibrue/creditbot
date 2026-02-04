import sqlite3
from datetime import datetime, date, timedelta
from typing import Optional
import config


def get_connection():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Initialize the database tables."""
    conn = get_connection()
    cursor = conn.cursor()

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            discord_id TEXT PRIMARY KEY,
            username TEXT,
            total_credits INTEGER DEFAULT 0,
            weekly_credits INTEGER DEFAULT 0,
            current_streak INTEGER DEFAULT 0,
            longest_streak INTEGER DEFAULT 0,
            total_lab_minutes INTEGER DEFAULT 0,
            last_checkin_date DATE
        )
    """)

    # Check-ins table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT,
            checkin_time TIMESTAMP,
            checkout_time TIMESTAMP,
            credits_earned INTEGER DEFAULT 0,
            message_id TEXT,
            FOREIGN KEY (discord_id) REFERENCES users(discord_id)
        )
    """)

    # Credit transactions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT,
            amount INTEGER,
            reason TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (discord_id) REFERENCES users(discord_id)
        )
    """)

    # Weekly history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start DATE,
            discord_id TEXT,
            credits INTEGER,
            rank INTEGER,
            FOREIGN KEY (discord_id) REFERENCES users(discord_id)
        )
    """)

    # Daily checkin messages (to track which message is today's checkin)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_checkin_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE,
            channel_id TEXT,
            date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Magic smoke votes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS magic_smoke_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_discord_id TEXT,
            voter_discord_id TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            applied INTEGER DEFAULT 0,
            UNIQUE(target_discord_id, voter_discord_id, applied)
        )
    """)

    # Meme posts tracking (for daily limit)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meme_credits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT,
            date DATE,
            UNIQUE(discord_id, date)
        )
    """)

    # Roasted messages tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS roasted_messages (
            message_id TEXT PRIMARY KEY,
            discord_id TEXT,
            applied INTEGER DEFAULT 0
        )
    """)

    # Notebook submissions tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notebook_submissions (
            message_id TEXT PRIMARY KEY,
            discord_id TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved INTEGER DEFAULT 0,
            result TEXT DEFAULT NULL
        )
    """)

    # Weekend bonus tracking (once per day)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekend_bonus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT,
            date DATE,
            UNIQUE(discord_id, date)
        )
    """)

    # Streak bonus tracking (once per day)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS streak_bonus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT,
            date DATE,
            UNIQUE(discord_id, date)
        )
    """)

    conn.commit()
    conn.close()


# ============ User Operations ============

def get_or_create_user(discord_id: str, username: str) -> dict:
    """Get a user or create them if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,))
    user = cursor.fetchone()

    if not user:
        cursor.execute(
            "INSERT INTO users (discord_id, username) VALUES (?, ?)",
            (discord_id, username)
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,))
        user = cursor.fetchone()

    conn.close()
    return dict(user)


def update_username(discord_id: str, username: str):
    """Update a user's username."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET username = ? WHERE discord_id = ?",
        (username, discord_id)
    )
    conn.commit()
    conn.close()


def get_user(discord_id: str) -> Optional[dict]:
    """Get a user by discord ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None


# ============ Credit Operations ============

def add_credits(discord_id: str, username: str, amount: int, reason: str):
    """Add credits to a user and log the transaction."""
    conn = get_connection()
    cursor = conn.cursor()

    # Ensure user exists
    get_or_create_user(discord_id, username)

    # Update credits
    cursor.execute("""
        UPDATE users
        SET total_credits = total_credits + ?,
            weekly_credits = weekly_credits + ?
        WHERE discord_id = ?
    """, (amount, amount, discord_id))

    # Log transaction
    cursor.execute(
        "INSERT INTO transactions (discord_id, amount, reason) VALUES (?, ?, ?)",
        (discord_id, amount, reason)
    )

    conn.commit()
    conn.close()


def get_transactions(discord_id: str, limit: int = 10) -> list:
    """Get recent transactions for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM transactions
        WHERE discord_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (discord_id, limit))
    transactions = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return transactions


# ============ Check-in Operations ============

def start_checkin(discord_id: str, username: str, message_id: str) -> int:
    """Start a check-in session. Returns the checkin ID."""
    conn = get_connection()
    cursor = conn.cursor()

    # Ensure user exists
    get_or_create_user(discord_id, username)

    # Check if already checked in (no checkout time)
    cursor.execute("""
        SELECT id FROM checkins
        WHERE discord_id = ? AND checkout_time IS NULL
    """, (discord_id,))
    existing = cursor.fetchone()

    if existing:
        conn.close()
        return existing['id']  # Already checked in

    # Create new checkin
    cursor.execute("""
        INSERT INTO checkins (discord_id, checkin_time, message_id)
        VALUES (?, ?, ?)
    """, (discord_id, datetime.now(), message_id))

    checkin_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return checkin_id


def end_checkin(discord_id: str) -> Optional[dict]:
    """End a check-in session. Returns checkin info with credits earned."""
    conn = get_connection()
    cursor = conn.cursor()

    # Find active checkin
    cursor.execute("""
        SELECT * FROM checkins
        WHERE discord_id = ? AND checkout_time IS NULL
        ORDER BY checkin_time DESC LIMIT 1
    """, (discord_id,))
    checkin = cursor.fetchone()

    if not checkin:
        conn.close()
        return None

    checkin_time = datetime.fromisoformat(checkin['checkin_time'])
    checkout_time = datetime.now()
    duration_minutes = int((checkout_time - checkin_time).total_seconds() / 60)

    # Calculate credits (1 per 30 minutes)
    credits_earned = (duration_minutes // 30) * config.CREDITS["lab_time_per_30_min"]

    # Check for weekend bonus (once per day only)
    weekend_bonus_given = False
    if checkout_time.weekday() >= 5:  # Saturday = 5, Sunday = 6
        if can_earn_weekend_bonus(discord_id):
            credits_earned += config.CREDITS["weekend_warrior"]
            record_weekend_bonus(discord_id)
            weekend_bonus_given = True

    # Update checkin record
    cursor.execute("""
        UPDATE checkins
        SET checkout_time = ?, credits_earned = ?
        WHERE id = ?
    """, (checkout_time, credits_earned, checkin['id']))

    # Update user stats
    cursor.execute("""
        UPDATE users
        SET total_lab_minutes = total_lab_minutes + ?,
            total_credits = total_credits + ?,
            weekly_credits = weekly_credits + ?
        WHERE discord_id = ?
    """, (duration_minutes, credits_earned, credits_earned, discord_id))

    # Log transaction
    if credits_earned > 0:
        cursor.execute(
            "INSERT INTO transactions (discord_id, amount, reason) VALUES (?, ?, ?)",
            (discord_id, credits_earned, f"Lab time: {duration_minutes} minutes")
        )

    conn.commit()
    conn.close()

    return {
        "checkin_time": checkin_time,
        "checkout_time": checkout_time,
        "duration_minutes": duration_minutes,
        "credits_earned": credits_earned,
        "weekend_bonus": weekend_bonus_given
    }


def get_active_checkin(discord_id: str) -> Optional[dict]:
    """Get the active checkin for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM checkins
        WHERE discord_id = ? AND checkout_time IS NULL
        ORDER BY checkin_time DESC LIMIT 1
    """, (discord_id,))
    checkin = cursor.fetchone()
    conn.close()
    return dict(checkin) if checkin else None


def get_all_checked_in() -> list:
    """Get all users currently checked in."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.discord_id, c.checkin_time, u.username
        FROM checkins c
        JOIN users u ON c.discord_id = u.discord_id
        WHERE c.checkout_time IS NULL
        ORDER BY c.checkin_time ASC
    """)
    checked_in = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return checked_in


def get_checkin_history(discord_id: str, limit: int = 10) -> list:
    """Get a user's check-in history."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM checkins
        WHERE discord_id = ?
        ORDER BY checkin_time DESC
        LIMIT ?
    """, (discord_id, limit))
    history = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return history


def get_stale_checkins(hours: int = 12) -> list:
    """Get checkins that have been open for too long."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(hours=hours)
    cursor.execute("""
        SELECT c.*, u.username FROM checkins c
        JOIN users u ON c.discord_id = u.discord_id
        WHERE c.checkout_time IS NULL AND c.checkin_time < ?
    """, (cutoff,))
    checkins = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return checkins


def force_checkout(checkin_id: int, penalty: int):
    """Force checkout a stale checkin with penalty."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,))
    checkin = cursor.fetchone()

    if checkin:
        cursor.execute("""
            UPDATE checkins SET checkout_time = ?, credits_earned = ?
            WHERE id = ?
        """, (datetime.now(), penalty, checkin_id))

        cursor.execute("""
            UPDATE users SET total_credits = total_credits + ?,
                           weekly_credits = weekly_credits + ?
            WHERE discord_id = ?
        """, (penalty, penalty, checkin['discord_id']))

        cursor.execute(
            "INSERT INTO transactions (discord_id, amount, reason) VALUES (?, ?, ?)",
            (checkin['discord_id'], penalty, "Forgot to check out (auto-checkout)")
        )

    conn.commit()
    conn.close()


def force_checkout_no_points(checkin_id: int):
    """Force checkout a stale checkin without giving any points (no penalty, no reward)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,))
    checkin = cursor.fetchone()

    if checkin:
        # Just close the session with 0 credits earned
        cursor.execute("""
            UPDATE checkins SET checkout_time = ?, credits_earned = 0
            WHERE id = ?
        """, (datetime.now(), checkin_id))

        # No transaction logged - they simply don't earn anything

    conn.commit()
    conn.close()


# ============ Daily Checkin Message Operations ============

def save_daily_checkin_message(message_id: str, channel_id: str):
    """Save today's checkin message."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO daily_checkin_messages (message_id, channel_id, date)
        VALUES (?, ?, ?)
    """, (message_id, channel_id, date.today()))
    conn.commit()
    conn.close()


def get_today_checkin_message() -> Optional[dict]:
    """Get today's checkin message."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM daily_checkin_messages WHERE date = ?
    """, (date.today(),))
    msg = cursor.fetchone()
    conn.close()
    return dict(msg) if msg else None


def is_checkin_message(message_id: str) -> bool:
    """Check if a message ID is a checkin message."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM daily_checkin_messages WHERE message_id = ?
    """, (message_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


# ============ Streak Operations ============

def update_streak(discord_id: str, username: str) -> int:
    """Update user's streak. Returns the new streak value."""
    conn = get_connection()
    cursor = conn.cursor()

    user = get_or_create_user(discord_id, username)
    today = date.today()
    last_checkin = user.get('last_checkin_date')

    if last_checkin:
        last_checkin = date.fromisoformat(last_checkin) if isinstance(last_checkin, str) else last_checkin
        days_diff = (today - last_checkin).days

        if days_diff == 0:
            # Already checked in today
            conn.close()
            return user['current_streak']
        elif days_diff == 1:
            # Consecutive day
            new_streak = user['current_streak'] + 1
        else:
            # Streak broken
            new_streak = 1
    else:
        new_streak = 1

    # Update user
    cursor.execute("""
        UPDATE users
        SET current_streak = ?,
            longest_streak = MAX(longest_streak, ?),
            last_checkin_date = ?
        WHERE discord_id = ?
    """, (new_streak, new_streak, today, discord_id))

    conn.commit()
    conn.close()
    return new_streak


def is_first_checkin_today() -> bool:
    """Check if no one has checked in today yet."""
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today()
    cursor.execute("""
        SELECT 1 FROM checkins
        WHERE DATE(checkin_time) = ?
        LIMIT 1
    """, (today,))
    result = cursor.fetchone()
    conn.close()
    return result is None


# ============ Leaderboard Operations ============

def get_weekly_leaderboard(limit: int = 10) -> list:
    """Get the weekly leaderboard."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT discord_id, username, weekly_credits, total_lab_minutes, current_streak
        FROM users
        WHERE weekly_credits > 0
        ORDER BY weekly_credits DESC
        LIMIT ?
    """, (limit,))
    leaderboard = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return leaderboard


def get_all_time_leaderboard(limit: int = 10) -> list:
    """Get the all-time leaderboard."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT discord_id, username, total_credits, total_lab_minutes, longest_streak
        FROM users
        WHERE total_credits > 0
        ORDER BY total_credits DESC
        LIMIT ?
    """, (limit,))
    leaderboard = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return leaderboard


def get_most_lab_hours_this_week() -> Optional[dict]:
    """Get the user with most lab hours this week."""
    conn = get_connection()
    cursor = conn.cursor()
    week_start = date.today() - timedelta(days=date.today().weekday())
    cursor.execute("""
        SELECT u.discord_id, u.username, SUM(c.credits_earned) as week_minutes
        FROM checkins c
        JOIN users u ON c.discord_id = u.discord_id
        WHERE DATE(c.checkin_time) >= ?
        GROUP BY c.discord_id
        ORDER BY week_minutes DESC
        LIMIT 1
    """, (week_start,))
    result = cursor.fetchone()
    conn.close()
    return dict(result) if result else None


def reset_weekly_credits():
    """Reset weekly credits for all users. Called at end of week."""
    conn = get_connection()
    cursor = conn.cursor()

    # Save this week's results to history
    week_start = date.today() - timedelta(days=date.today().weekday())
    cursor.execute("""
        INSERT INTO weekly_history (week_start, discord_id, credits, rank)
        SELECT ?, discord_id, weekly_credits,
               ROW_NUMBER() OVER (ORDER BY weekly_credits DESC)
        FROM users WHERE weekly_credits > 0
    """, (week_start,))

    # Reset weekly credits
    cursor.execute("UPDATE users SET weekly_credits = 0")

    conn.commit()
    conn.close()


def penalize_supreme_leader(discord_id: str, penalty: int = -15):
    """Give the Supreme Leader a penalty to start the new week behind."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET weekly_credits = ?
        WHERE discord_id = ?
    """, (penalty, discord_id))

    # Also log the transaction
    cursor.execute("""
        INSERT INTO transactions (discord_id, amount, reason, timestamp)
        VALUES (?, ?, ?, ?)
    """, (discord_id, penalty, "Supreme Leader handicap - heavy is the crown", datetime.now()))

    conn.commit()
    conn.close()


def get_previous_week_credits(discord_id: str) -> int:
    """Get a user's credits from the previous week."""
    conn = get_connection()
    cursor = conn.cursor()
    last_week = date.today() - timedelta(days=7)
    week_start = last_week - timedelta(days=last_week.weekday())
    cursor.execute("""
        SELECT credits FROM weekly_history
        WHERE discord_id = ? AND week_start = ?
    """, (discord_id, week_start))
    result = cursor.fetchone()
    conn.close()
    return result['credits'] if result else 0


# ============ Magic Smoke Operations ============

def has_voted_magic_smoke(target_id: str, voter_id: str) -> bool:
    """Check if a user has already voted for magic smoke against a target (within 24 hours)."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(hours=24)
    cursor.execute("""
        SELECT 1 FROM magic_smoke_votes
        WHERE target_discord_id = ? AND voter_discord_id = ? AND applied = 0
        AND timestamp > ?
    """, (target_id, voter_id, cutoff))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def get_magic_smoke_voters(target_id: str) -> list:
    """Get list of voter IDs who have voted against a target (within 24 hours)."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(hours=24)
    cursor.execute("""
        SELECT voter_discord_id FROM magic_smoke_votes
        WHERE target_discord_id = ? AND applied = 0 AND timestamp > ?
    """, (target_id, cutoff))
    voters = [row['voter_discord_id'] for row in cursor.fetchall()]
    conn.close()
    return voters


def add_magic_smoke_vote(target_id: str, voter_id: str) -> int:
    """Add a magic smoke vote. Returns total votes for target, or -1 if already voted."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(hours=24)

    # Check if already voted (within 24 hours)
    cursor.execute("""
        SELECT 1 FROM magic_smoke_votes
        WHERE target_discord_id = ? AND voter_discord_id = ? AND applied = 0
        AND timestamp > ?
    """, (target_id, voter_id, cutoff))
    if cursor.fetchone():
        conn.close()
        return -1  # Already voted

    cursor.execute("""
        INSERT INTO magic_smoke_votes (target_discord_id, voter_discord_id)
        VALUES (?, ?)
    """, (target_id, voter_id))
    conn.commit()

    # Count only votes from last 24 hours
    cursor.execute("""
        SELECT COUNT(*) as votes FROM magic_smoke_votes
        WHERE target_discord_id = ? AND applied = 0 AND timestamp > ?
    """, (target_id, cutoff))
    votes = cursor.fetchone()['votes']
    conn.close()
    return votes


def apply_magic_smoke(target_id: str):
    """Apply magic smoke penalty and mark votes as applied."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE magic_smoke_votes SET applied = 1
        WHERE target_discord_id = ? AND applied = 0
    """, (target_id,))
    conn.commit()
    conn.close()


# ============ Meme Credit Operations ============

def can_earn_meme_credit(discord_id: str) -> bool:
    """Check if user can earn meme credit today."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM meme_credits WHERE discord_id = ? AND date = ?
    """, (discord_id, date.today()))
    result = cursor.fetchone()
    conn.close()
    return result is None


def record_meme_credit(discord_id: str):
    """Record that user earned meme credit today."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO meme_credits (discord_id, date) VALUES (?, ?)
        """, (discord_id, date.today()))
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # Already recorded
    conn.close()


# ============ Roasted Operations ============

def track_roasted_message(message_id: str, discord_id: str):
    """Track a message for roasting."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO roasted_messages (message_id, discord_id) VALUES (?, ?)
        """, (message_id, discord_id))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()


def is_message_roasted(message_id: str) -> bool:
    """Check if a message has already been roasted."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT applied FROM roasted_messages WHERE message_id = ?
    """, (message_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result['applied'] == 1


def mark_message_roasted(message_id: str):
    """Mark a message as roasted."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE roasted_messages SET applied = 1 WHERE message_id = ?
    """, (message_id,))
    conn.commit()
    conn.close()


def get_roasted_message_author(message_id: str) -> Optional[str]:
    """Get the author of a tracked message."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT discord_id FROM roasted_messages WHERE message_id = ?
    """, (message_id,))
    result = cursor.fetchone()
    conn.close()
    return result['discord_id'] if result else None


# ============ Notebook Operations ============

def track_notebook_submission(message_id: str, discord_id: str):
    """Track a notebook submission for voting."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO notebook_submissions (message_id, discord_id)
            VALUES (?, ?)
        """, (message_id, discord_id))
        conn.commit()
    except:
        pass  # Already tracked
    conn.close()


def get_notebook_submission(message_id: str) -> Optional[dict]:
    """Get a notebook submission by message ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM notebook_submissions WHERE message_id = ?
    """, (message_id,))
    result = cursor.fetchone()
    conn.close()
    return dict(result) if result else None


def resolve_notebook_submission(message_id: str, result: str):
    """Mark a notebook submission as resolved with result (approved/rejected)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE notebook_submissions SET resolved = 1, result = ?
        WHERE message_id = ?
    """, (result, message_id))
    conn.commit()
    conn.close()


# ============ Weekend Bonus Operations ============

def can_earn_weekend_bonus(discord_id: str) -> bool:
    """Check if user can earn weekend bonus today."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM weekend_bonus WHERE discord_id = ? AND date = ?
    """, (discord_id, date.today()))
    result = cursor.fetchone()
    conn.close()
    return result is None


def record_weekend_bonus(discord_id: str):
    """Record that user earned weekend bonus today."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO weekend_bonus (discord_id, date) VALUES (?, ?)
        """, (discord_id, date.today()))
        conn.commit()
    except:
        pass  # Already recorded
    conn.close()


# ============ Streak Bonus Operations ============

def can_earn_streak_bonus(discord_id: str) -> bool:
    """Check if user can earn streak bonus today."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM streak_bonus WHERE discord_id = ? AND date = ?
    """, (discord_id, date.today()))
    result = cursor.fetchone()
    conn.close()
    return result is None


def record_streak_bonus(discord_id: str):
    """Record that user earned streak bonus today."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO streak_bonus (discord_id, date) VALUES (?, ?)
        """, (discord_id, date.today()))
        conn.commit()
    except:
        pass  # Already recorded
    conn.close()


# ============ Audit Operations ============

def audit_user_credits(discord_id: str) -> dict:
    """Audit a user's credits by recalculating from transactions.
    Returns dict with calculated vs stored values and the difference."""
    conn = get_connection()
    cursor = conn.cursor()

    # Get stored values
    cursor.execute("""
        SELECT total_credits, weekly_credits, username FROM users WHERE discord_id = ?
    """, (discord_id,))
    user = cursor.fetchone()

    if not user:
        conn.close()
        return None

    # Calculate total from all transactions
    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE discord_id = ?
    """, (discord_id,))
    calculated_total = cursor.fetchone()['total']

    # Calculate weekly (transactions from this week's start)
    week_start = date.today() - timedelta(days=date.today().weekday())
    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) as weekly FROM transactions
        WHERE discord_id = ? AND DATE(timestamp) >= ?
    """, (discord_id, week_start))
    calculated_weekly = cursor.fetchone()['weekly']

    conn.close()

    return {
        'discord_id': discord_id,
        'username': user['username'],
        'stored_total': user['total_credits'],
        'calculated_total': calculated_total,
        'total_diff': user['total_credits'] - calculated_total,
        'stored_weekly': user['weekly_credits'],
        'calculated_weekly': calculated_weekly,
        'weekly_diff': user['weekly_credits'] - calculated_weekly
    }


def fix_user_credits(discord_id: str) -> dict:
    """Recalculate and fix a user's credits from transaction history."""
    audit = audit_user_credits(discord_id)
    if not audit:
        return None

    conn = get_connection()
    cursor = conn.cursor()

    # Update to calculated values
    cursor.execute("""
        UPDATE users SET total_credits = ?, weekly_credits = ?
        WHERE discord_id = ?
    """, (audit['calculated_total'], audit['calculated_weekly'], discord_id))

    conn.commit()
    conn.close()

    return audit


def audit_all_users() -> list:
    """Audit all users and return those with discrepancies."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT discord_id FROM users")
    users = [row['discord_id'] for row in cursor.fetchall()]
    conn.close()

    discrepancies = []
    for user_id in users:
        audit = audit_user_credits(user_id)
        if audit and (audit['total_diff'] != 0 or audit['weekly_diff'] != 0):
            discrepancies.append(audit)

    return discrepancies


def fix_all_credits() -> list:
    """Fix all user credits from transaction history."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT discord_id FROM users")
    users = [row['discord_id'] for row in cursor.fetchall()]
    conn.close()

    fixed = []
    for user_id in users:
        result = fix_user_credits(user_id)
        if result and (result['total_diff'] != 0 or result['weekly_diff'] != 0):
            fixed.append(result)

    return fixed
