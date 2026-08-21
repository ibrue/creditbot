"""Tests for the reaction-driven check-in flow — the bot's most-used path.

The cog's task loops are not started; the handlers are driven directly.
"""
import asyncio

import pytest

import config
from cogs.checkin import CheckinCog


class FakeUser:
    def __init__(self, user_id=1, name="alice", dm_fails=False):
        self.id = user_id
        self.name = name
        self.dm_fails = dm_fails
        self.messages = []

    async def send(self, content):
        if self.dm_fails:
            raise RuntimeError("cannot DM this user (DMs closed)")
        self.messages.append(content)


class FakeBot:
    def __init__(self, users=None, bot_id=999):
        self.user = FakeUser(user_id=bot_id, name="creditbot")
        self._users = {u.id: u for u in (users or [])}

    async def fetch_user(self, user_id):
        return self._users[user_id]


class FakePayload:
    def __init__(self, user_id, message_id, emoji):
        self.user_id = user_id
        self.message_id = message_id
        self.emoji = emoji


@pytest.fixture
def cog():
    return object.__new__(CheckinCog)


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    """Pin the clock outside night-owl hours unless a test says otherwise."""
    import cogs.checkin as checkin_mod
    monkeypatch.setattr(checkin_mod, "is_night_owl_time", lambda hour: False)


def drive(cog, bot, coro_factory):
    cog.bot = bot
    asyncio.run(coro_factory())


# -------------------------------------------------------------- check-in

def test_reaction_checkin_awards_first_arrival(cog, db):
    user = FakeUser()
    drive(cog, FakeBot([user]), lambda: cog._handle_checkin(user, 111))

    assert db.get_active_checkin("1") is not None
    assert db.get_user("1")["total_credits"] == config.CREDITS["first_arrival"]
    assert "Checked in" in user.messages[0]


def test_second_person_gets_no_first_arrival(cog, db):
    alice, bob = FakeUser(1, "alice"), FakeUser(2, "bob")
    bot = FakeBot([alice, bob])

    drive(cog, bot, lambda: cog._handle_checkin(alice, 111))
    drive(cog, bot, lambda: cog._handle_checkin(bob, 111))

    assert db.get_user("2")["total_credits"] == 0


def test_night_owl_bonus(cog, db, monkeypatch):
    import cogs.checkin as checkin_mod
    monkeypatch.setattr(checkin_mod, "is_night_owl_time", lambda hour: True)
    user = FakeUser()

    drive(cog, FakeBot([user]), lambda: cog._handle_checkin(user, 111))

    expected = config.CREDITS["first_arrival"] + config.CREDITS["night_owl"]
    assert db.get_user("1")["total_credits"] == expected


def test_double_checkin_is_refused_without_awarding_again(cog, db):
    user = FakeUser()
    bot = FakeBot([user])

    drive(cog, bot, lambda: cog._handle_checkin(user, 111))
    credits_after_first = db.get_user("1")["total_credits"]
    drive(cog, bot, lambda: cog._handle_checkin(user, 111))

    assert db.get_user("1")["total_credits"] == credits_after_first
    assert "already checked in" in user.messages[-1].lower()


def test_checkin_still_recorded_when_the_dm_fails(cog, db):
    """A member with DMs closed must still get their check-in and credits."""
    user = FakeUser(dm_fails=True)

    drive(cog, FakeBot([user]), lambda: cog._handle_checkin(user, 111))

    assert db.get_active_checkin("1") is not None
    assert db.get_user("1")["total_credits"] == config.CREDITS["first_arrival"]


# ------------------------------------------------------------- check-out

def test_checkout_reports_duration_and_credits(cog, db, backdate):
    user = FakeUser()
    bot = FakeBot([user])
    drive(cog, bot, lambda: cog._handle_checkin(user, 111))
    backdate("1", 90)

    drive(cog, bot, lambda: cog._handle_checkout(user))

    assert db.get_active_checkin("1") is None
    assert "1 hour 30 minutes" in user.messages[-1]


def test_checkout_without_checkin_says_so(cog, db):
    user = FakeUser()

    drive(cog, FakeBot([user]), lambda: cog._handle_checkout(user))

    assert "not checked in" in user.messages[-1].lower()


def test_checkout_still_recorded_when_the_dm_fails(cog, db, backdate):
    user = FakeUser(dm_fails=True)
    bot = FakeBot([user])
    drive(cog, bot, lambda: cog._handle_checkin(user, 111))
    backdate("1", 60)

    drive(cog, bot, lambda: cog._handle_checkout(user))

    assert db.get_active_checkin("1") is None
    assert db.get_user("1")["total_lab_minutes"] == pytest.approx(60, abs=1)


# ------------------------------------------------------- reaction routing

def test_the_bots_own_reaction_is_ignored(cog, db):
    db.save_daily_checkin_message("111", "222")
    bot = FakeBot([])

    drive(cog, bot, lambda: cog.on_raw_reaction_add(
        FakePayload(user_id=bot.user.id, message_id=111, emoji=config.CHECKIN_EMOJI)))

    assert db.get_all_checked_in() == []


def test_reactions_on_other_messages_are_ignored(cog, db):
    user = FakeUser()

    drive(cog, FakeBot([user]), lambda: cog.on_raw_reaction_add(
        FakePayload(user_id=1, message_id=999, emoji=config.CHECKIN_EMOJI)))

    assert db.get_all_checked_in() == []


def test_the_checkin_emoji_checks_a_member_in(cog, db):
    db.save_daily_checkin_message("111", "222")
    user = FakeUser()

    drive(cog, FakeBot([user]), lambda: cog.on_raw_reaction_add(
        FakePayload(user_id=1, message_id=111, emoji=config.CHECKIN_EMOJI)))

    assert db.get_active_checkin("1") is not None


def test_the_checkout_emoji_checks_a_member_out(cog, db):
    db.save_daily_checkin_message("111", "222")
    user = FakeUser()
    bot = FakeBot([user])
    drive(cog, bot, lambda: cog.on_raw_reaction_add(
        FakePayload(user_id=1, message_id=111, emoji=config.CHECKIN_EMOJI)))

    drive(cog, bot, lambda: cog.on_raw_reaction_add(
        FakePayload(user_id=1, message_id=111, emoji=config.CHECKOUT_EMOJI)))

    assert db.get_active_checkin("1") is None


def test_an_unrelated_emoji_does_nothing(cog, db):
    db.save_daily_checkin_message("111", "222")
    user = FakeUser()

    drive(cog, FakeBot([user]), lambda: cog.on_raw_reaction_add(
        FakePayload(user_id=1, message_id=111, emoji="🎉")))

    assert db.get_all_checked_in() == []


# ------------------------------- the invariant checkin_logic exists to keep

def test_a_kiosk_checkin_earns_exactly_what_a_discord_checkin_earns(cog, db, monkeypatch):
    """checkin_logic.py exists so the kiosk and Discord award the same
    credits. Run both against identical state and compare."""
    import checkin_logic
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)

    # Discord path
    user = FakeUser(1, "alice")
    drive(cog, FakeBot([user]), lambda: cog._handle_checkin(user, 111))
    discord_credits = db.get_user("1")["total_credits"]
    discord_streak = db.get_user("1")["current_streak"]

    # Kiosk path, from the same starting state
    db.init_database()
    conn = db.get_connection()
    conn.execute("DELETE FROM users")
    conn.execute("DELETE FROM checkins")
    conn.execute("DELETE FROM transactions")
    conn.commit()
    conn.close()

    checkin_logic.perform_checkin("2", "bob")
    kiosk_credits = db.get_user("2")["total_credits"]
    kiosk_streak = db.get_user("2")["current_streak"]

    assert kiosk_credits == discord_credits
    assert kiosk_streak == discord_streak
