"""Tests for the admin commands that rewrite people's scores.

/add-credits, /remove-credits, /set-credits and /audit are the only ways a
human can change the books by hand, so they get the same scrutiny as the
automatic paths.
"""
import asyncio

import pytest

from cogs.leaderboard import LeaderboardCog
from cogs.social_credit import SocialCreditCog


class FakeMember:
    def __init__(self, member_id, name="alice", bot=False):
        self.id = member_id
        self.name = name
        self.bot = bot

    @property
    def mention(self):
        return f"<@{self.id}>"


class FakeResponse:
    def __init__(self):
        self.messages = []
        self._done = False

    async def send_message(self, content=None, embed=None, ephemeral=False):
        self.messages.append({"content": content, "embed": embed})
        self._done = True

    async def defer(self, ephemeral=False):
        self._done = True

    def is_done(self):
        return self._done


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, embed=None, ephemeral=False):
        self.messages.append({"content": content, "embed": embed})


class FakeInteraction:
    def __init__(self, interaction_id=1, user=None):
        self.id = interaction_id
        self.user = user or FakeMember(100, "admin")
        self.response = FakeResponse()
        self.followup = FakeFollowup()

    def said(self):
        """Everything the command replied, as one string."""
        parts = []
        for message in self.response.messages + self.followup.messages:
            if message["content"]:
                parts.append(message["content"])
            if message["embed"] is not None:
                parts.append(message["embed"].description or "")
        return "\n".join(parts)


def make_cog(cls):
    cog = object.__new__(cls)
    cog._processed_interactions = set()
    cog._max_tracked_interactions = 1000
    return cog


@pytest.fixture
def social():
    return make_cog(SocialCreditCog)


@pytest.fixture
def board():
    return make_cog(LeaderboardCog)


def call(command, cog, interaction, *args, **kwargs):
    return asyncio.run(command.callback(cog, interaction, *args, **kwargs))


# ----------------------------------------------------------- /add-credits

def test_add_credits_grants_and_logs(social, db):
    target = FakeMember(1, "alice")
    interaction = FakeInteraction()

    call(SocialCreditCog.add_credits_cmd, social, interaction, target, 10, "great work")

    assert db.get_user("1")["total_credits"] == 10
    transaction = db.get_transactions("1")[0]
    assert transaction["amount"] == 10
    assert "admin" in transaction["reason"].lower()
    assert "great work" in transaction["reason"]


@pytest.mark.parametrize("amount", [0, -5])
def test_add_credits_refuses_non_positive_amounts(social, db, amount):
    target = FakeMember(1, "alice")
    interaction = FakeInteraction()

    call(SocialCreditCog.add_credits_cmd, social, interaction, target, amount, "x")

    assert "positive" in interaction.said().lower()
    assert db.get_user("1") is None


def test_add_credits_refuses_bots(social, db):
    target = FakeMember(1, "botty", bot=True)
    interaction = FakeInteraction()

    call(SocialCreditCog.add_credits_cmd, social, interaction, target, 10, "x")

    assert "bot" in interaction.said().lower()
    assert db.get_user("1") is None


def test_a_repeated_interaction_does_not_grant_twice(social, db):
    """Discord can deliver the same interaction more than once."""
    target = FakeMember(1, "alice")

    call(SocialCreditCog.add_credits_cmd, social, FakeInteraction(7), target, 10, "x")
    call(SocialCreditCog.add_credits_cmd, social, FakeInteraction(7), target, 10, "x")

    assert db.get_user("1")["total_credits"] == 10


# -------------------------------------------------------- /remove-credits

def test_remove_credits_deducts(social, db):
    db.add_credits("1", "alice", 20, "earned")
    target = FakeMember(1, "alice")

    call(SocialCreditCog.remove_credits, social, FakeInteraction(), target, 5, "oops")

    assert db.get_user("1")["total_credits"] == 15


def test_remove_credits_can_go_negative(social, db):
    target = FakeMember(1, "alice")

    call(SocialCreditCog.remove_credits, social, FakeInteraction(), target, 5, "x")

    assert db.get_user("1")["total_credits"] == -5


@pytest.mark.parametrize("amount", [0, -5])
def test_remove_credits_refuses_non_positive_amounts(social, db, amount):
    """A negative 'removal' would silently be a grant."""
    db.add_credits("1", "alice", 20, "earned")
    target = FakeMember(1, "alice")
    interaction = FakeInteraction()

    call(SocialCreditCog.remove_credits, social, interaction, target, amount, "x")

    assert "positive" in interaction.said().lower()
    assert db.get_user("1")["total_credits"] == 20


def test_remove_credits_refuses_bots(social, db):
    target = FakeMember(1, "botty", bot=True)
    interaction = FakeInteraction()

    call(SocialCreditCog.remove_credits, social, interaction, target, 5, "x")

    assert "bot" in interaction.said().lower()


# ----------------------------------------------------------- /set-credits

def test_set_credits_reaches_the_exact_total(board, db):
    db.add_credits("1", "alice", 20, "earned")
    target = FakeMember(1, "alice")

    call(LeaderboardCog.set_credits, board, FakeInteraction(), target, 50, "correction")

    assert db.get_user("1")["total_credits"] == 50


def test_set_credits_works_downward(board, db):
    db.add_credits("1", "alice", 100, "earned")
    target = FakeMember(1, "alice")

    call(LeaderboardCog.set_credits, board, FakeInteraction(), target, 30, "correction")

    assert db.get_user("1")["total_credits"] == 30


def test_set_credits_on_a_new_member(board, db):
    target = FakeMember(1, "alice")

    call(LeaderboardCog.set_credits, board, FakeInteraction(), target, 25, "seed")

    assert db.get_user("1")["total_credits"] == 25


def test_set_credits_accepts_a_negative_total(board, db):
    target = FakeMember(1, "alice")

    call(LeaderboardCog.set_credits, board, FakeInteraction(), target, -10, "penalty")

    assert db.get_user("1")["total_credits"] == -10


def test_set_credits_to_the_current_value_is_a_no_op(board, db):
    db.add_credits("1", "alice", 20, "earned")
    target = FakeMember(1, "alice")
    interaction = FakeInteraction()

    call(LeaderboardCog.set_credits, board, interaction, target, 20, "x")

    assert "already has" in interaction.said()
    assert len(db.get_transactions("1")) == 1  # no adjustment logged


def test_set_credits_refuses_bots(board, db):
    target = FakeMember(1, "botty", bot=True)
    interaction = FakeInteraction()

    call(LeaderboardCog.set_credits, board, interaction, target, 50, "x")

    assert "bot" in interaction.said().lower()
    assert db.get_user("1") is None


def test_set_credits_leaves_the_books_balanced(board, db):
    """It adjusts by a logged difference, so the audit must stay clean."""
    db.add_credits("1", "alice", 20, "earned")
    target = FakeMember(1, "alice")

    call(LeaderboardCog.set_credits, board, FakeInteraction(), target, 50, "correction")

    assert db.audit_user_credits("1")["total_diff"] == 0
    assert db.audit_all_users() == []


# ----------------------------------------------------------------- /audit

def test_audit_reports_a_clean_ledger(board, db):
    db.add_credits("1", "alice", 20, "earned")
    interaction = FakeInteraction()

    call(LeaderboardCog.audit, board, interaction, False)

    assert "No discrepancies" in interaction.said()


def _introduce_drift(db, discord_id="1", stored=999):
    conn = db.get_connection()
    conn.execute("UPDATE users SET total_credits = ? WHERE discord_id = ?",
                 (stored, discord_id))
    conn.commit()
    conn.close()


def test_audit_reports_drift_without_changing_anything(board, db):
    db.add_credits("1", "alice", 20, "earned")
    _introduce_drift(db)
    interaction = FakeInteraction()

    call(LeaderboardCog.audit, board, interaction, False)

    assert "discrepanc" in interaction.said().lower()
    assert db.get_user("1")["total_credits"] == 999  # untouched without fix


def test_audit_fix_repairs_the_ledger(board, db):
    db.add_credits("1", "alice", 20, "earned")
    _introduce_drift(db)

    interaction = FakeInteraction()
    call(LeaderboardCog.audit, board, interaction, True)

    assert db.get_user("1")["total_credits"] == 20
    assert "Fixed" in interaction.said()


def test_audit_fix_on_a_clean_ledger_says_so(board, db):
    db.add_credits("1", "alice", 20, "earned")
    interaction = FakeInteraction()

    call(LeaderboardCog.audit, board, interaction, True)

    assert "No discrepancies" in interaction.said()
    assert db.get_user("1")["total_credits"] == 20


def test_audit_summarizes_rather_than_listing_everyone(board, db):
    """A big lab must not blow past Discord's message limit."""
    for i in range(15):
        db.add_credits(str(i), f"user{i}", 10, "earned")
        _introduce_drift(db, str(i), 500)
    interaction = FakeInteraction()

    call(LeaderboardCog.audit, board, interaction, False)

    said = interaction.said()
    assert "and 5 more" in said
    assert len(said) < 2000  # Discord's limit
