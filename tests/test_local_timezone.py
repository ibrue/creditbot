"""Tests for the timezone the scheduled posts follow (config.LOCAL_TZ).

discord.py reads a time without a tzinfo as UTC, so with TZ=America/Chicago
the "5 PM" check-in used to post at noon. The schedule has to follow the
same clock as datetime.now(), which everything else in the bot uses.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import config

ROOT = Path(__file__).resolve().parent.parent

# UTC+14 all year: no developer's or CI runner's clock is set to it
FARAWAY = "Pacific/Kiritimati"


def clock_offset():
    return datetime.now().astimezone().utcoffset()


def test_the_timezone_database_is_available():
    """Windows has no timezone database of its own; zoneinfo reads the
    tzdata package from requirements.txt instead."""
    chicago = ZoneInfo("America/Chicago")

    assert chicago.utcoffset(datetime(2026, 7, 1)) == timedelta(hours=-5)
    assert chicago.utcoffset(datetime(2026, 12, 1)) == timedelta(hours=-6)


def test_without_tz_the_computers_clock_is_used(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)

    assert config.local_timezone().utcoffset(None) == clock_offset()


def test_an_unknown_tz_falls_back_to_the_clock(monkeypatch, capsys):
    monkeypatch.setenv("TZ", "Not/AZone")

    assert config.local_timezone().utcoffset(None) == clock_offset()
    assert "Unknown timezone" in capsys.readouterr().out


def test_a_tz_that_disagrees_with_the_clock_is_not_trusted(monkeypatch, capsys):
    """Without Docker TZ comes from .env and the computer keeps the clock, so
    a stale TZ would put the posts on a different clock from night owl and
    weekends."""
    monkeypatch.setenv("TZ", FARAWAY)  # read after startup: the clock ignores it

    assert config.local_timezone().utcoffset(None) == clock_offset()
    assert "disagrees with this computer's clock" in capsys.readouterr().out


# The bot as docker-compose starts it, in a fresh process: TZ is set before
# Python starts, so it sets the clock and is read when the cogs are defined.
FIRE_TIMES = """
import json
from datetime import date, datetime, timezone

import config
from cogs.checkin import CheckinCog
from cogs.leaderboard import LeaderboardCog

loops = {
    "check-in": CheckinCog.daily_checkin_post,
    "weekly": LeaderboardCog.weekly_announcement,
    "leaderboard": LeaderboardCog.daily_leaderboard,
}
summer, winter = date(2026, 7, 1), date(2026, 12, 1)
print(json.dumps({
    "zone": str(config.LOCAL_TZ),
    **{name: [datetime.combine(day, loop.time[0]).astimezone(timezone.utc).strftime("%H:%M")
              for day in (summer, winter)]
       for name, loop in loops.items()},
}))
"""


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows' C runtime cannot set its clock from a TZ zone name")
def test_with_tz_set_the_posts_fire_at_local_hours_all_year():
    result = subprocess.run(
        [sys.executable, "-c", FIRE_TIMES], cwd=ROOT,
        env={**os.environ, "TZ": "America/Chicago"},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    assert json.loads(result.stdout) == {
        "zone": "America/Chicago",
        # In UTC, for 1 July (CDT, UTC-5) and 1 December (CST, UTC-6)
        "check-in": ["22:00", "23:00"],     # 5 PM
        "weekly": ["23:00", "00:00"],       # 6 PM
        "leaderboard": ["00:00", "01:00"],  # 7 PM
    }


# The server as START.bat runs it on Windows: no TZ when Python starts, then
# .env's TZ=America/Chicago arrives through load_dotenv() in config.
CLOCK = """
import os, sys, time
from datetime import datetime

if sys.argv[1:] == ["with-env-file"]:
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: os.environ.update(TZ="America/Chicago")
    import config
print(time.strftime("%Z"), datetime.now().astimezone().utcoffset())
"""


def clock(*args):
    env = {key: value for key, value in os.environ.items() if key != "TZ"}
    env["PYTHONIOENCODING"] = "utf-8"  # config's warnings carry emoji
    result = subprocess.run(
        [sys.executable, "-c", CLOCK, *args], cwd=ROOT, env=env,
        capture_output=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1]


def test_tz_from_the_env_file_does_not_move_the_computers_clock():
    """Windows' C runtime reads TZ the first time it needs local time, and
    would take America/Chicago for UTC (an hour ahead in summer), moving
    night owl, weekends and stored times with it."""
    assert clock("with-env-file") == clock()
