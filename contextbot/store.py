"""Context Bot's own SQLite database: what the team said, and what was reported.

Separate from creditbot's social_credit.db. Times are stored as UTC ISO
strings, so they sort and compare as text.
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.astimezone()  # naive means local time
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_connection():
    parent = os.path.dirname(config.DATABASE_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(config.DATABASE_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id   TEXT PRIMARY KEY,
            channel_id   TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            author_id    TEXT NOT NULL,
            author_name  TEXT NOT NULL,
            content      TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            kind         TEXT NOT NULL DEFAULT 'chat',  -- chat | update
            edited       INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_id);

        CREATE TABLE IF NOT EXISTS reports (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            kind       TEXT NOT NULL,   -- report | daily | scorecard | judge
            subject_id TEXT,            -- the member, for a one-person judgment
            days       INTEGER,
            created_at TEXT NOT NULL,
            text       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


# ----------------------------------------------------------------- messages

def log_message(message_id: str, channel_id: str, channel_name: str,
                author_id: str, author_name: str, content: str,
                created_at: datetime, kind: str = "chat") -> bool:
    """Store a message. Returns False if it was already stored (backfill)."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT OR IGNORE INTO messages (message_id, channel_id, channel_name, "
        "author_id, author_name, content, created_at, kind) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(message_id), str(channel_id), channel_name, str(author_id),
         author_name, content, _iso(created_at), kind),
    )
    conn.commit()
    conn.close()
    return cur.rowcount == 1


def edit_message(message_id: str, content: str) -> bool:
    # Discord also reports an "edit" when it unfurls a link; only a change
    # to the text counts
    conn = get_connection()
    cur = conn.execute(
        "UPDATE messages SET content = ?, edited = 1 "
        "WHERE message_id = ? AND content != ?",
        (content, str(message_id), content),
    )
    conn.commit()
    conn.close()
    return cur.rowcount == 1


def delete_message(message_id: str) -> bool:
    """Someone deleted a message in Discord — forget it here too."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM messages WHERE message_id = ?", (str(message_id),))
    conn.commit()
    conn.close()
    return cur.rowcount == 1


def forget_author(author_id: str) -> int:
    conn = get_connection()
    cur = conn.execute("DELETE FROM messages WHERE author_id = ?", (str(author_id),))
    conn.commit()
    conn.close()
    return cur.rowcount


def messages_since(since: datetime, author_id: Optional[str] = None) -> list[dict]:
    """Messages from `since` onward, oldest first."""
    query = "SELECT * FROM messages WHERE created_at >= ?"
    params: list = [_iso(since)]
    if author_id is not None:
        query += " AND author_id = ?"
        params.append(str(author_id))
    query += " ORDER BY created_at, message_id"
    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def prune(days: int) -> int:
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM messages WHERE created_at < ?",
        (_iso(now_utc() - timedelta(days=days)),),
    )
    conn.execute(
        "DELETE FROM reports WHERE created_at < ?",
        (_iso(now_utc() - timedelta(days=days)),),
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def stats() -> dict:
    conn = get_connection()
    total, oldest, newest = conn.execute(
        "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM messages"
    ).fetchone()
    week = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE created_at >= ?",
        (_iso(now_utc() - timedelta(days=7)),),
    ).fetchone()[0]
    channels = conn.execute(
        "SELECT channel_name, COUNT(*) AS n FROM messages "
        "GROUP BY channel_id ORDER BY n DESC LIMIT 10"
    ).fetchall()
    conn.close()
    return {
        "total": total, "oldest": oldest, "newest": newest, "last_7_days": week,
        "channels": [(r["channel_name"], r["n"]) for r in channels],
    }


# ------------------------------------------------------------------ reports

def save_report(kind: str, text: str, days: Optional[int] = None,
                subject_id: Optional[str] = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO reports (kind, subject_id, days, created_at, text) "
        "VALUES (?, ?, ?, ?, ?)",
        (kind, None if subject_id is None else str(subject_id), days,
         _iso(now_utc()), text),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def last_report(kind: str, subject_id: Optional[str] = None,
                max_age_days: int = 45) -> Optional[dict]:
    """The most recent report of a kind — so a judgment can show the trend."""
    query = "SELECT * FROM reports WHERE kind = ? AND created_at >= ?"
    params: list = [kind, _iso(now_utc() - timedelta(days=max_age_days))]
    if subject_id is None:
        query += " AND subject_id IS NULL"
    else:
        query += " AND subject_id = ?"
        params.append(str(subject_id))
    query += " ORDER BY created_at DESC, id DESC LIMIT 1"
    conn = get_connection()
    row = conn.execute(query, params).fetchone()
    conn.close()
    return dict(row) if row else None


# ----------------------------------------------------------------- settings

def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_connection()
    if value:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    else:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()
    conn.close()
