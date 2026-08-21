"""Tests for the pure formatting/ranking helpers."""
import pytest

from utils.helpers import (
    format_credits,
    format_duration,
    get_credit_tier,
    get_rank_emoji,
    get_streak_message,
    time_until_next,
)


@pytest.mark.parametrize("minutes,expected", [
    (0, "0 minutes"),
    (1, "1 minute"),
    (59, "59 minutes"),
    (60, "1 hour"),
    (61, "1 hour 1 minute"),
    (120, "2 hours"),
    (135, "2 hours 15 minutes"),
])
def test_format_duration(minutes, expected):
    assert format_duration(minutes) == expected


@pytest.mark.parametrize("credits,expected", [
    (5, "+5"), (0, "0"), (-3, "-3"),
])
def test_format_credits(credits, expected):
    assert format_credits(credits) == expected


@pytest.mark.parametrize("rank,expected", [
    (1, "🏆"), (2, "🥈"), (3, "🥉"), (4, "#4"), (17, "#17"),
])
def test_get_rank_emoji(rank, expected):
    assert get_rank_emoji(rank) == expected


@pytest.mark.parametrize("credits,tier", [
    (1500, "Supreme Leader"),
    (1000, "Supreme Leader"),
    (999, "Comrade General"),
    (500, "Comrade General"),
    (250, "Party Member"),
    (100, "Loyal Citizen"),
    (50, "Promising Worker"),
    (10, "New Recruit"),
    (0, "Unranked"),
])
def test_get_credit_tier_boundaries(credits, tier):
    assert get_credit_tier(credits)[0] == tier


def test_negative_credits_get_debt_collector_tier():
    assert get_credit_tier(-1)[0] == "Debt Collector"


@pytest.mark.parametrize("streak,fragment", [
    (30, "LEGENDARY"), (14, "Two week"), (7, "One week"),
    (3, "momentum"), (1, "Keep it up"), (0, ""),
])
def test_get_streak_message(streak, fragment):
    assert fragment in get_streak_message(streak)


def test_time_until_next_is_within_a_day():
    delta = time_until_next(9)
    assert 0 < delta.total_seconds() <= 24 * 3600
