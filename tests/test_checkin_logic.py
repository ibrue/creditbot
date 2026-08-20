"""Tests for the shared kiosk check-in/check-out logic.

A kiosk check-in must earn exactly what a Discord check-in earns.
"""
from datetime import date, timedelta

import pytest

import config
import checkin_logic
from conftest import expected_weekend_bonus


@pytest.fixture(autouse=True)
def _isolated(db):
    """Every test in this module runs against the throwaway database."""
    return db


def test_first_checkin_of_the_day_gets_the_first_arrival_bonus(db, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)

    result = checkin_logic.perform_checkin("1", "alice")

    assert result["status"] == "checked_in"
    assert result["streak"] == 1
    assert any("First arrival" in b for b in result["bonuses"])
    assert db.get_user("1")["total_credits"] == config.CREDITS["first_arrival"]


def test_second_person_gets_no_first_arrival_bonus(db, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)

    checkin_logic.perform_checkin("1", "alice")
    result = checkin_logic.perform_checkin("2", "bob")

    assert not any("First arrival" in b for b in result["bonuses"])
    assert db.get_user("2")["total_credits"] == 0


def test_night_owl_bonus_applies_after_hours(db, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: True)

    result = checkin_logic.perform_checkin("1", "alice")

    assert any("Night owl" in b for b in result["bonuses"])
    expected = config.CREDITS["first_arrival"] + config.CREDITS["night_owl"]
    assert db.get_user("1")["total_credits"] == expected


def test_double_checkin_is_rejected_without_awarding_again(db, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)

    checkin_logic.perform_checkin("1", "alice")
    credits_after_first = db.get_user("1")["total_credits"]
    result = checkin_logic.perform_checkin("1", "alice")

    assert result["status"] == "already_checked_in"
    assert "minutes_so_far" in result
    assert db.get_user("1")["total_credits"] == credits_after_first


def test_streak_bonus_is_awarded_once_per_day(db, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)

    # Day 1
    checkin_logic.perform_checkin("1", "alice")
    checkin_logic.perform_checkout("1")

    # Pretend yesterday was day 1, so today is a consecutive day
    conn = db.get_connection()
    conn.execute("UPDATE users SET last_checkin_date = ? WHERE discord_id = '1'",
                 (date.today() - timedelta(days=1),))
    conn.commit()
    conn.close()

    before = db.get_user("1")["total_credits"]
    result = checkin_logic.perform_checkin("1", "alice")

    assert result["streak"] == 2
    assert any("Streak day 2" in b for b in result["bonuses"])
    assert db.get_user("1")["total_credits"] == before + config.CREDITS["streak_bonus"]


def test_streak_bonus_is_not_paid_twice_in_one_day(db, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)
    checkin_logic.perform_checkin("0", "opener")  # consume the first-arrival bonus
    db.record_streak_bonus("1")

    conn = db.get_connection()
    db.get_or_create_user("1", "alice")
    conn.execute("UPDATE users SET last_checkin_date = ?, current_streak = 1 "
                 "WHERE discord_id = '1'", (date.today() - timedelta(days=1),))
    conn.commit()
    conn.close()

    before = db.get_user("1")["total_credits"]
    result = checkin_logic.perform_checkin("1", "alice")

    assert result["streak"] == 2
    assert any("already claimed today" in b for b in result["bonuses"])
    assert db.get_user("1")["total_credits"] == before


def test_streak_bonus_is_capped_at_seven_days(db, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)
    checkin_logic.perform_checkin("0", "opener")  # consume the first-arrival bonus

    db.get_or_create_user("1", "alice")
    conn = db.get_connection()
    conn.execute("UPDATE users SET last_checkin_date = ?, current_streak = 8 "
                 "WHERE discord_id = '1'", (date.today() - timedelta(days=1),))
    conn.commit()
    conn.close()

    before = db.get_user("1")["total_credits"]
    result = checkin_logic.perform_checkin("1", "alice")

    assert result["streak"] == 9
    assert any("capped at 7 days" in b for b in result["bonuses"])
    assert db.get_user("1")["total_credits"] == before


def test_checkout_reports_duration_and_credits(db, backdate, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)

    checkin_logic.perform_checkin("1", "alice")
    backdate("1", 90)
    bonus = expected_weekend_bonus(db, "1")

    result = checkin_logic.perform_checkout("1")

    assert result["status"] == "checked_out"
    assert result["duration_minutes"] == pytest.approx(90, abs=1)
    assert result["duration"] == "1 hour 30 minutes"
    assert result["credits_earned"] == 3 + bonus


def test_checkout_without_checkin(db):
    assert checkin_logic.perform_checkout("1")["status"] == "not_checked_in"


def test_kiosk_checkin_is_recorded_with_its_source(db, monkeypatch):
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)

    checkin_logic.perform_checkin("1", "alice", source="kiosk")

    conn = db.get_connection()
    row = conn.execute(
        "SELECT message_id FROM checkins WHERE discord_id = '1'").fetchone()
    conn.close()
    assert row["message_id"] == "kiosk"
