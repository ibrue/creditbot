"""Shared pytest fixtures.

Every test runs against a throwaway SQLite file so the suite never
touches a real social_credit.db.
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A freshly initialized database, isolated per test."""
    import config
    import database

    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "test.db"))
    database.init_database()
    return database


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    """Isolated kiosk upload directory."""
    import config

    path = tmp_path / "uploads"
    monkeypatch.setattr(config, "KIOSK_UPLOADS_DIR", str(path))
    return path


@pytest.fixture
def client(db, uploads_dir, monkeypatch):
    """A TestClient for the kiosk API with a known API key."""
    from fastapi.testclient import TestClient

    import api

    monkeypatch.setattr(api, "API_KEY", "test-key")
    with TestClient(api.app) as c:
        c.headers.update({"X-API-Key": "test-key"})
        yield c


@pytest.fixture
def backdate(db):
    """Move a user's open check-in back in time to simulate a long session."""
    import config

    def _backdate(discord_id, minutes):
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.execute(
            "UPDATE checkins SET checkin_time = ? "
            "WHERE discord_id = ? AND checkout_time IS NULL",
            (datetime.now() - timedelta(minutes=minutes), discord_id),
        )
        conn.commit()
        conn.close()
    return _backdate


def expected_weekend_bonus(db, discord_id):
    """The weekend bonus a checkout right now would add, if any."""
    import config

    if datetime.now().weekday() >= 5 and db.can_earn_weekend_bonus(discord_id):
        return config.CREDITS["weekend_warrior"]
    return 0
