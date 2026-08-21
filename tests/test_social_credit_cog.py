"""Tests for the social-credit cog: duplicate guarding and notebook voting."""
import asyncio

import pytest

import config
from cogs.social_credit import SocialCreditCog


class FakeUser:
    def __init__(self, user_id, name="alice"):
        self.id = user_id
        self.name = name

    @property
    def mention(self):
        return f"<@{self.id}>"


class FakeReaction:
    def __init__(self, emoji, users):
        self.emoji = emoji
        self._users = users

    def users(self):
        async def gen():
            for user in self._users:
                yield user
        return gen()


class FakeMessage:
    def __init__(self, message_id, reactions=None):
        self.id = message_id
        self.reactions = reactions or []
        self.replies = []

    async def reply(self, content):
        self.replies.append(content)


class FakeBot:
    def __init__(self, users=None, bot_id=999):
        self.user = FakeUser(bot_id, "creditbot")
        self._users = {u.id: u for u in (users or [])}

    async def fetch_user(self, user_id):
        return self._users[user_id]


@pytest.fixture
def cog():
    instance = object.__new__(SocialCreditCog)
    instance._processed_interactions = set()
    instance._max_tracked_interactions = 1000
    return instance


def drive(cog, bot, coro_factory):
    cog.bot = bot
    return asyncio.run(coro_factory())


# ------------------------------------------------------ duplicate guarding

def test_first_interaction_is_not_a_duplicate(cog):
    assert cog._check_duplicate(1) is False


def test_repeated_interaction_is_a_duplicate(cog):
    cog._check_duplicate(1)
    assert cog._check_duplicate(1) is True


def test_distinct_interactions_are_independent(cog):
    assert cog._check_duplicate(1) is False
    assert cog._check_duplicate(2) is False
    assert cog._check_duplicate(1) is True


def test_the_tracking_set_is_bounded(cog):
    """It must not grow without limit — the bot runs for weeks at a time."""
    cog._max_tracked_interactions = 10
    for i in range(50):
        cog._check_duplicate(i)

    assert len(cog._processed_interactions) <= 10


def test_the_most_recent_interaction_survives_a_cleanup(cog):
    cog._max_tracked_interactions = 10
    for i in range(50):
        cog._check_duplicate(i)

    assert cog._check_duplicate(49) is True


# --------------------------------------------------------- vote tallying

def test_votes_are_counted(cog, db):
    voters = [FakeUser(2), FakeUser(3), FakeUser(4)]
    message = FakeMessage(111, [FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI, voters)])

    up, down = drive(cog, FakeBot(), lambda: cog._tally_notebook_votes(message, "1"))

    assert (up, down) == (3, 0)


def test_the_author_cannot_vote_for_themselves(cog, db):
    voters = [FakeUser(1), FakeUser(2)]  # user 1 is the author
    message = FakeMessage(111, [FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI, voters)])

    up, down = drive(cog, FakeBot(), lambda: cog._tally_notebook_votes(message, "1"))

    assert up == 1


def test_the_bots_own_reaction_is_not_counted(cog, db):
    bot = FakeBot()
    voters = [bot.user, FakeUser(2)]
    message = FakeMessage(111, [FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI, voters)])

    up, down = drive(cog, bot, lambda: cog._tally_notebook_votes(message, "1"))

    assert up == 1


def test_upvotes_and_downvotes_are_tallied_separately(cog, db):
    message = FakeMessage(111, [
        FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI, [FakeUser(2), FakeUser(3)]),
        FakeReaction(config.NOTEBOOK_DOWNVOTE_EMOJI, [FakeUser(4)]),
    ])

    up, down = drive(cog, FakeBot(), lambda: cog._tally_notebook_votes(message, "1"))

    assert (up, down) == (2, 1)


def test_unrelated_reactions_are_ignored(cog, db):
    message = FakeMessage(111, [
        FakeReaction("🎉", [FakeUser(2), FakeUser(3)]),
        FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI, [FakeUser(4)]),
    ])

    up, down = drive(cog, FakeBot(), lambda: cog._tally_notebook_votes(message, "1"))

    assert (up, down) == (1, 0)


# ------------------------------------------------------ notebook outcomes

def _submission(db, message_id="111", author="1"):
    db.track_notebook_submission(message_id, author)
    return db.get_notebook_submission(message_id)


def test_enough_upvotes_approves_and_pays(cog, db):
    submission = _submission(db)
    author = FakeUser(1, "alice")
    voters = [FakeUser(i) for i in range(2, 2 + config.NOTEBOOK_VOTES_REQUIRED)]
    message = FakeMessage(111, [FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI, voters)])

    drive(cog, FakeBot([author]), lambda: cog._evaluate_notebook(message, submission))

    assert db.get_user("1")["total_credits"] == config.CREDITS["documentation"]
    assert db.get_notebook_submission("111")["result"] == "approved"
    assert "Approved" in message.replies[0]


def test_enough_downvotes_rejects_and_penalizes(cog, db):
    submission = _submission(db)
    author = FakeUser(1, "alice")
    voters = [FakeUser(i) for i in range(2, 2 + config.NOTEBOOK_DOWNVOTES_REQUIRED)]
    message = FakeMessage(111, [FakeReaction(config.NOTEBOOK_DOWNVOTE_EMOJI, voters)])

    drive(cog, FakeBot([author]), lambda: cog._evaluate_notebook(message, submission))

    assert db.get_user("1")["total_credits"] == config.NOTEBOOK_DOWNVOTE_PENALTY
    assert db.get_notebook_submission("111")["result"] == "rejected"
    assert "Rejected" in message.replies[0]


def test_too_few_votes_resolves_nothing(cog, db):
    submission = _submission(db)
    author = FakeUser(1, "alice")
    voters = [FakeUser(2)]
    message = FakeMessage(111, [FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI, voters)])

    drive(cog, FakeBot([author]), lambda: cog._evaluate_notebook(message, submission))

    assert db.get_user("1") is None or db.get_user("1")["total_credits"] == 0
    assert db.get_notebook_submission("111")["resolved"] == 0
    assert message.replies == []


def test_downvotes_cancel_upvotes(cog, db):
    """Net votes decide, so a contested entry does not pay out."""
    submission = _submission(db)
    author = FakeUser(1, "alice")
    message = FakeMessage(111, [
        FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI,
                     [FakeUser(i) for i in range(2, 2 + config.NOTEBOOK_VOTES_REQUIRED)]),
        FakeReaction(config.NOTEBOOK_DOWNVOTE_EMOJI, [FakeUser(50)]),
    ])

    drive(cog, FakeBot([author]), lambda: cog._evaluate_notebook(message, submission))

    assert db.get_notebook_submission("111")["resolved"] == 0
    assert message.replies == []


def test_a_second_evaluation_cannot_pay_twice(cog, db):
    """Two near-simultaneous reactions must not both award credits."""
    submission = _submission(db)
    author = FakeUser(1, "alice")
    voters = [FakeUser(i) for i in range(2, 2 + config.NOTEBOOK_VOTES_REQUIRED)]
    message = FakeMessage(111, [FakeReaction(config.NOTEBOOK_UPVOTE_EMOJI, voters)])
    bot = FakeBot([author])

    drive(cog, bot, lambda: cog._evaluate_notebook(message, submission))
    drive(cog, bot, lambda: cog._evaluate_notebook(message, submission))

    assert db.get_user("1")["total_credits"] == config.CREDITS["documentation"]
    assert len(message.replies) == 1


# -------------------------------------------------------- supreme leader

class FakeRole:
    def __init__(self, name):
        self.name = name


class FakeMember:
    def __init__(self, roles):
        self.roles = roles


def test_supreme_leader_is_recognized_by_role(cog):
    member = FakeMember([FakeRole("Member"), FakeRole(config.WINNER_ROLE_NAME)])
    assert cog._is_supreme_leader(member) is True


def test_a_member_without_the_role_is_not_supreme_leader(cog):
    member = FakeMember([FakeRole("Member")])
    assert cog._is_supreme_leader(member) is False


def test_a_member_with_no_roles_is_not_supreme_leader(cog):
    assert cog._is_supreme_leader(FakeMember([])) is False
