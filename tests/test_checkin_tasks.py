"""Tests for the check-in cog's scheduled tasks and admin commands."""
import asyncio
from datetime import datetime

import pytest

import config
from cogs.checkin import CheckinCog


class FakeUser:
    def __init__(self, user_id, name="alice", bot=False, dm_fails=False):
        self.id = user_id
        self.name = name
        self.bot = bot
        self.dm_fails = dm_fails
        self.messages = []

    @property
    def mention(self):
        return f"<@{self.id}>"

    async def send(self, content):
        if self.dm_fails:
            raise RuntimeError("DMs closed")
        self.messages.append(content)


class FakeMessage:
    def __init__(self, message_id=555):
        self.id = message_id
        self.reactions_added = []

    async def add_reaction(self, emoji):
        self.reactions_added.append(emoji)


class FakeChannel:
    def __init__(self, channel_id=777, send_fails=False):
        self.id = channel_id
        self.sent = []
        self.send_fails = send_fails
        self.message = FakeMessage()

    async def send(self, content=None, embed=None):
        if self.send_fails:
            raise RuntimeError("Discord is down")
        self.sent.append({"content": content, "embed": embed})
        return self.message


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content=None, embed=None, ephemeral=False):
        self.messages.append({"content": content, "embed": embed})


class FakeInteraction:
    def __init__(self, interaction_id=1, user=None):
        self.id = interaction_id
        self.user = user or FakeUser(100, "admin")
        self.response = FakeResponse()

    def said(self):
        parts = []
        for message in self.response.messages:
            if message["content"]:
                parts.append(message["content"])
            if message["embed"] is not None:
                parts.append(message["embed"].description or "")
        return "\n".join(parts)


class FakeBot:
    def __init__(self, channel=None, users=None):
        self.user = FakeUser(999, "creditbot")
        self._channel = channel
        self._users = {u.id: u for u in (users or [])}

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_user(self, user_id):
        if user_id not in self._users:
            raise RuntimeError("unknown user")
        return self._users[user_id]


@pytest.fixture
def cog():
    instance = object.__new__(CheckinCog)
    instance._processed_interactions = set()
    instance._max_tracked_interactions = 1000
    return instance


def drive(cog, bot, coro_factory):
    cog.bot = bot
    return asyncio.run(coro_factory())


def call(command, cog, bot, interaction, *args, **kwargs):
    cog.bot = bot
    return asyncio.run(command.callback(cog, interaction, *args, **kwargs))


# ----------------------------------------------------- daily check-in post

def test_the_daily_message_is_posted_and_seeded_with_reactions(cog, db, monkeypatch):
    monkeypatch.setattr(config, "CHECKIN_CHANNEL_ID", 777)
    channel = FakeChannel()

    drive(cog, FakeBot(channel), lambda: cog._daily_checkin_post())

    assert len(channel.sent) == 1
    assert channel.message.reactions_added == [config.CHECKIN_EMOJI, config.CHECKOUT_EMOJI]
    assert db.is_checkin_message("555") is True


def test_no_daily_post_without_a_configured_channel(cog, db, monkeypatch):
    monkeypatch.setattr(config, "CHECKIN_CHANNEL_ID", 0)
    channel = FakeChannel()

    drive(cog, FakeBot(channel), lambda: cog._daily_checkin_post())

    assert channel.sent == []


def test_an_unresolvable_channel_is_survived(cog, db, monkeypatch):
    monkeypatch.setattr(config, "CHECKIN_CHANNEL_ID", 777)

    drive(cog, FakeBot(None), lambda: cog._daily_checkin_post())  # must not raise


def test_a_failed_daily_post_does_not_kill_the_loop(cog, db, monkeypatch):
    """An escaping exception would stop the task until the next restart."""
    monkeypatch.setattr(config, "CHECKIN_CHANNEL_ID", 777)
    channel = FakeChannel(send_fails=True)

    drive(cog, FakeBot(channel), lambda: cog.daily_checkin_post())  # swallowed

    assert channel.sent == []


# ---------------------------------------------------------- auto-checkout

def test_a_stale_session_is_closed(cog, db, backdate):
    db.start_checkin("1", "alice", "msg")
    backdate("1", (config.AUTO_CHECKOUT_HOURS + 1) * 60)
    user = FakeUser(1, "alice")

    drive(cog, FakeBot(users=[user]), lambda: cog._auto_checkout())

    assert db.get_active_checkin("1") is None


def test_auto_checkout_awards_nothing(cog, db, backdate):
    """Current behavior: forgetting to check out voids the session's credits
    rather than applying the -2 penalty the README describes.
    config.CREDITS['forgot_checkout'] is currently unused."""
    db.start_checkin("1", "alice", "msg")
    backdate("1", (config.AUTO_CHECKOUT_HOURS + 1) * 60)
    user = FakeUser(1, "alice")

    drive(cog, FakeBot(users=[user]), lambda: cog._auto_checkout())

    assert db.get_user("1")["total_credits"] == 0  # no credits, and no penalty


def test_a_fresh_session_is_left_alone(cog, db, backdate):
    db.start_checkin("1", "alice", "msg")
    backdate("1", 60)
    user = FakeUser(1, "alice")

    drive(cog, FakeBot(users=[user]), lambda: cog._auto_checkout())

    assert db.get_active_checkin("1") is not None


def test_the_member_is_told_they_were_auto_checked_out(cog, db, backdate):
    db.start_checkin("1", "alice", "msg")
    backdate("1", (config.AUTO_CHECKOUT_HOURS + 1) * 60)
    user = FakeUser(1, "alice")

    drive(cog, FakeBot(users=[user]), lambda: cog._auto_checkout())

    assert "forgot to check out" in user.messages[0].lower()


def test_an_undeliverable_dm_does_not_block_the_sweep(cog, db, backdate):
    db.start_checkin("1", "alice", "msg")
    db.start_checkin("2", "bob", "msg")
    backdate("1", (config.AUTO_CHECKOUT_HOURS + 1) * 60)
    backdate("2", (config.AUTO_CHECKOUT_HOURS + 1) * 60)
    alice = FakeUser(1, "alice", dm_fails=True)
    bob = FakeUser(2, "bob")

    drive(cog, FakeBot(users=[alice, bob]), lambda: cog._auto_checkout())

    assert db.get_all_checked_in() == []  # both closed despite the failed DM


def test_the_sweep_also_prunes(cog, db, backdate):
    db.track_roasted_message("old-msg", "1")
    conn = db.get_connection()
    conn.execute("UPDATE roasted_messages SET created_at = NULL")
    conn.commit()
    conn.close()

    drive(cog, FakeBot(), lambda: cog.auto_checkout())

    assert db.get_roasted_message_author("old-msg") is None


# --------------------------------------------------------- /force-checkout

def test_force_checkout_awards_points_by_default(cog, db, backdate):
    db.start_checkin("1", "alice", "msg")
    backdate("1", 90)
    target = FakeUser(1, "alice")
    interaction = FakeInteraction()

    call(CheckinCog.force_checkout, cog, FakeBot(), interaction, target, True)

    assert db.get_active_checkin("1") is None
    assert db.get_user("1")["total_credits"] >= 3
    assert "1 hour 30 minutes" in interaction.said()


def test_force_checkout_can_withhold_points(cog, db, backdate):
    db.start_checkin("1", "alice", "msg")
    backdate("1", 90)
    target = FakeUser(1, "alice")
    interaction = FakeInteraction()

    call(CheckinCog.force_checkout, cog, FakeBot(), interaction, target, False)

    assert db.get_active_checkin("1") is None
    assert db.get_user("1")["total_credits"] == 0
    assert "no points awarded" in interaction.said().lower()


def test_force_checkout_on_someone_not_checked_in(cog, db):
    target = FakeUser(1, "alice")
    interaction = FakeInteraction()

    call(CheckinCog.force_checkout, cog, FakeBot(), interaction, target, True)

    assert "not currently checked in" in interaction.said()


def test_force_checkout_survives_an_undeliverable_dm(cog, db, backdate):
    db.start_checkin("1", "alice", "msg")
    backdate("1", 60)
    target = FakeUser(1, "alice", dm_fails=True)

    call(CheckinCog.force_checkout, cog, FakeBot(), FakeInteraction(), target, True)

    assert db.get_active_checkin("1") is None


# ---------------------------------------------------------------- /whos-in

def test_whos_in_on_an_empty_lab(cog, db):
    interaction = FakeInteraction()

    call(CheckinCog.whos_in, cog, FakeBot(), interaction, )

    assert "Nobody is currently checked in" in interaction.said()


def test_whos_in_lists_everyone_with_their_time(cog, db, backdate):
    db.start_checkin("1", "alice", "msg")
    db.start_checkin("2", "bob", "msg")
    backdate("1", 90)
    interaction = FakeInteraction()

    call(CheckinCog.whos_in, cog, FakeBot(), interaction)

    embed = interaction.response.messages[0]["embed"]
    listing = "\n".join(f.value for f in embed.fields)
    assert "<@1>" in listing
    assert "<@2>" in listing
    assert "1h 30m" in listing
    assert "**2** people" in embed.description
