"""Tests for the weekly announcement and the Supreme Leader role handoff.

This is the cog that crowns the winner, resets the week, and applies the
handicap — the highest-stakes code in the project, since it rewrites
everyone's weekly score once a week.
"""
import asyncio
from datetime import datetime

import discord
import pytest

import config
from cogs.leaderboard import LeaderboardCog


class FakeRole:
    def __init__(self, name=None, role_id=7, members=None):
        self.name = name or config.WINNER_ROLE_NAME
        self.id = role_id
        self.members = members or []


class FakeMember:
    def __init__(self, member_id, name="alice", roles=None):
        self.id = member_id
        self.name = name
        self.roles = roles if roles is not None else []
        self.added = []
        self.removed = []

    @property
    def mention(self):
        return f"<@{self.id}>"

    async def add_roles(self, role, reason=None):
        self.added.append(role)
        self.roles.append(role)

    async def remove_roles(self, role, reason=None):
        self.removed.append(role)
        if role in self.roles:
            self.roles.remove(role)


class FakeGuild:
    def __init__(self, roles=None, members=None, chunked=True):
        self.roles = roles or []
        self._members = {m.id: m for m in (members or [])}
        self.chunked = chunked
        self.chunk_calls = 0
        self.created_roles = []

    def get_role(self, role_id):
        return next((r for r in self.roles if r.id == role_id), None)

    def get_member(self, member_id):
        return self._members.get(member_id)

    async def fetch_member(self, member_id):
        member = self._members.get(member_id)
        if member is None:
            raise discord.NotFound(_FakeResponse(404), "unknown member")
        return member

    async def chunk(self, cache=True):
        self.chunk_calls += 1
        self.chunked = True

    async def create_role(self, **kwargs):
        role = FakeRole(name=kwargs.get("name"), role_id=99)
        self.roles.append(role)
        self.created_roles.append(kwargs)
        return role


class _FakeResponse:
    def __init__(self, status):
        self.status = status
        self.reason = "fake"


class FakeChannel:
    def __init__(self, guild=None):
        self.guild = guild
        self.sent = []

    async def send(self, content=None, embed=None):
        self.sent.append({"content": content, "embed": embed})


class FakeBot:
    def __init__(self, channel=None):
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel


@pytest.fixture
def cog():
    instance = object.__new__(LeaderboardCog)
    instance._processed_interactions = set()
    instance._max_tracked_interactions = 1000
    return instance


@pytest.fixture(autouse=True)
def _announcement_config(monkeypatch):
    """Make today the announcement day and give it a channel."""
    monkeypatch.setattr(config, "WEEKLY_ANNOUNCEMENT_DAY",
                        datetime.now().strftime("%A").lower())
    monkeypatch.setattr(config, "ANNOUNCEMENTS_CHANNEL_ID", 555)
    monkeypatch.setattr(config, "WINNER_ROLE_ID", 0)


def drive(cog, bot, coro_factory):
    cog.bot = bot
    return asyncio.run(coro_factory())


# ------------------------------------------------------------ winner role

def test_the_role_is_created_when_missing(cog):
    guild = FakeGuild()
    winner = FakeMember(1, "alice")
    guild._members[1] = winner

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert guild.created_roles
    assert guild.created_roles[0]["name"] == config.WINNER_ROLE_NAME
    assert len(winner.added) == 1


def test_an_existing_role_is_reused(cog):
    role = FakeRole()
    guild = FakeGuild(roles=[role], members=[FakeMember(1, "alice")])

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert guild.created_roles == []


def test_a_configured_role_id_is_preferred(cog, monkeypatch):
    monkeypatch.setattr(config, "WINNER_ROLE_ID", 42)
    configured = FakeRole(name="Custom Crown", role_id=42)
    guild = FakeGuild(roles=[configured], members=[FakeMember(1, "alice")])

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert guild.created_roles == []
    assert guild._members[1].added[0] is configured


def test_the_role_is_revoked_from_the_previous_holder(cog):
    role = FakeRole()
    previous = FakeMember(2, "bob", roles=[role])
    role.members = [previous]
    new_winner = FakeMember(1, "alice")
    guild = FakeGuild(roles=[role], members=[previous, new_winner])

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert previous.removed == [role]
    assert new_winner.added == [role]


def test_a_repeat_winner_keeps_the_role_without_churn(cog):
    role = FakeRole()
    repeat = FakeMember(1, "alice", roles=[role])
    role.members = [repeat]
    guild = FakeGuild(roles=[role], members=[repeat])

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert repeat.removed == []
    assert repeat.added == []  # already had it


def test_every_previous_holder_is_revoked(cog):
    role = FakeRole()
    holders = [FakeMember(i, f"user{i}", roles=[role]) for i in (2, 3, 4)]
    role.members = list(holders)
    winner = FakeMember(1, "alice")
    guild = FakeGuild(roles=[role], members=holders + [winner])

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert all(h.removed == [role] for h in holders)


def test_the_guild_is_chunked_so_revocation_sees_everyone(cog):
    """role.members reads the member cache, which is empty after a restart."""
    role = FakeRole()
    guild = FakeGuild(roles=[role], members=[FakeMember(1, "alice")], chunked=False)

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert guild.chunk_calls == 1


def test_an_already_chunked_guild_is_not_rechunked(cog):
    role = FakeRole()
    guild = FakeGuild(roles=[role], members=[FakeMember(1, "alice")], chunked=True)

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert guild.chunk_calls == 0


def test_an_uncached_winner_is_fetched(cog):
    role = FakeRole()
    winner = FakeMember(1, "alice")
    guild = FakeGuild(roles=[role], members=[winner])
    guild.get_member = lambda member_id: None  # not in cache

    result = drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert result is winner


def test_a_missing_winner_is_reported_not_raised(cog):
    role = FakeRole()
    guild = FakeGuild(roles=[role], members=[])

    result = drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert result is None


def test_missing_role_permissions_are_survived(cog, monkeypatch):
    async def forbidden(guild):
        raise discord.Forbidden(_FakeResponse(403), "missing permissions")

    monkeypatch.setattr(cog, "get_or_create_winner_role", forbidden)
    guild = FakeGuild()

    result = drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert result is None


def test_a_failed_revocation_does_not_stop_the_crowning(cog):
    """One member the bot cannot demote must not cost the winner their role."""
    role = FakeRole()

    stubborn = FakeMember(2, "bob", roles=[role])

    async def refuse(role_, reason=None):
        raise RuntimeError("role hierarchy says no")

    stubborn.remove_roles = refuse
    role.members = [stubborn]
    winner = FakeMember(1, "alice")
    guild = FakeGuild(roles=[role], members=[stubborn, winner])

    drive(cog, FakeBot(), lambda: cog.update_winner_role(guild, "1"))

    assert winner.added == [role]


# ---------------------------------------------------- weekly announcement

def test_nothing_happens_on_another_day(cog, db, monkeypatch):
    monkeypatch.setattr(config, "WEEKLY_ANNOUNCEMENT_DAY", "not-a-real-day")
    db.add_credits("1", "alice", 10, "x")
    channel = FakeChannel(FakeGuild())

    drive(cog, FakeBot(channel), lambda: cog._weekly_announcement())

    assert channel.sent == []
    assert db.get_user("1")["weekly_credits"] == 10  # not reset


def test_a_quiet_week_still_resets(cog, db):
    channel = FakeChannel(FakeGuild())

    drive(cog, FakeBot(channel), lambda: cog._weekly_announcement())

    assert "No social credit activity" in channel.sent[0]["content"]
    assert db.get_last_weekly_reset() is not None


def test_the_announcement_crowns_and_resets(cog, db):
    db.add_credits("1", "alice", 50, "a good week")
    db.add_credits("2", "bob", 20, "solid week")
    role = FakeRole()
    winner = FakeMember(1, "alice")
    guild = FakeGuild(roles=[role], members=[winner, FakeMember(2, "bob")])
    channel = FakeChannel(guild)

    drive(cog, FakeBot(channel), lambda: cog._weekly_announcement())

    assert winner.added == [role]
    assert db.get_user("2")["weekly_credits"] == 0          # reset
    assert db.get_user("1")["weekly_credits"] == -15        # handicap
    assert db.get_user("1")["total_credits"] == 50 - 15     # and it costs them


def test_the_handicap_leaves_no_audit_drift(cog, db):
    """The whole announcement, end to end, must leave the books balanced."""
    db.add_credits("1", "alice", 50, "a good week")
    db.add_credits("2", "bob", 20, "solid week")
    guild = FakeGuild(roles=[FakeRole()],
                      members=[FakeMember(1, "alice"), FakeMember(2, "bob")])

    drive(cog, FakeBot(FakeChannel(guild)), lambda: cog._weekly_announcement())

    assert db.audit_all_users() == []


def test_the_top_three_are_announced_in_order(cog, db):
    db.add_credits("1", "alice", 50, "x")
    db.add_credits("2", "bob", 30, "x")
    db.add_credits("3", "carol", 10, "x")
    guild = FakeGuild(roles=[FakeRole()],
                      members=[FakeMember(i, n) for i, n in
                               ((1, "alice"), (2, "bob"), (3, "carol"))])
    channel = FakeChannel(guild)

    drive(cog, FakeBot(channel), lambda: cog._weekly_announcement())

    embed = channel.sent[0]["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert "<@1>" in fields["🏆 Supreme Leader"]
    assert "<@2>" in fields["🥈 Comrade of the People"]
    assert "<@3>" in fields["🥉 Rising Star"]


def test_most_improved_compares_against_last_week(cog, db):
    from datetime import date, timedelta

    last_monday = date.today() - timedelta(days=date.today().weekday() + 7)
    conn = db.get_connection()
    conn.execute("INSERT INTO weekly_history (week_start, discord_id, credits, rank) "
                 "VALUES (?, '1', 45, 1)", (last_monday,))
    conn.execute("INSERT INTO weekly_history (week_start, discord_id, credits, rank) "
                 "VALUES (?, '2', 1, 2)", (last_monday,))
    conn.commit()
    conn.close()

    db.add_credits("1", "alice", 50, "x")   # +5 on last week
    db.add_credits("2", "bob", 30, "x")     # +29 on last week
    guild = FakeGuild(roles=[FakeRole()],
                      members=[FakeMember(1, "alice"), FakeMember(2, "bob")])
    channel = FakeChannel(guild)

    drive(cog, FakeBot(channel), lambda: cog._weekly_announcement())

    fields = {f.name: f.value for f in channel.sent[0]["embed"].fields}
    assert "<@2>" in fields["📈 Most Improved"]
    assert "+29" in fields["📈 Most Improved"]


def test_it_falls_back_to_the_checkin_channel(cog, db, monkeypatch):
    monkeypatch.setattr(config, "ANNOUNCEMENTS_CHANNEL_ID", 0)
    monkeypatch.setattr(config, "CHECKIN_CHANNEL_ID", 777)
    channel = FakeChannel(FakeGuild())

    drive(cog, FakeBot(channel), lambda: cog._weekly_announcement())

    assert channel.sent  # posted somewhere rather than silently dropped


def test_no_configured_channel_is_a_no_op(cog, db, monkeypatch):
    monkeypatch.setattr(config, "ANNOUNCEMENTS_CHANNEL_ID", 0)
    monkeypatch.setattr(config, "CHECKIN_CHANNEL_ID", 0)
    db.add_credits("1", "alice", 10, "x")

    drive(cog, FakeBot(FakeChannel(FakeGuild())), lambda: cog._weekly_announcement())

    assert db.get_user("1")["weekly_credits"] == 10  # nothing reset


def test_an_unresolvable_channel_does_not_reset(cog, db):
    """If the announcement cannot be posted, the week must not vanish."""
    db.add_credits("1", "alice", 10, "x")

    drive(cog, FakeBot(None), lambda: cog._weekly_announcement())

    assert db.get_user("1")["weekly_credits"] == 10
