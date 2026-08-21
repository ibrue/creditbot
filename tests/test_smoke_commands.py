"""Tests for the commands that move credits between people:
/thank, /magic-smoke, /supreme-smoke and /agree-smoke.
"""
import asyncio
from datetime import datetime, timedelta

import pytest

import config
from cogs.social_credit import SocialCreditCog


class FakeRole:
    def __init__(self, name):
        self.name = name


class FakeMember:
    def __init__(self, member_id, name="alice", bot=False, roles=None):
        self.id = member_id
        self.name = name
        self.bot = bot
        self.roles = roles or []

    @property
    def mention(self):
        return f"<@{self.id}>"


def leader(member_id=100, name="chief"):
    return FakeMember(member_id, name, roles=[FakeRole(config.WINNER_ROLE_NAME)])


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content=None, embed=None, ephemeral=False):
        self.messages.append({"content": content, "embed": embed})


class FakeInteraction:
    def __init__(self, interaction_id=1, user=None):
        self.id = interaction_id
        self.user = user or FakeMember(100, "voter")
        self.response = FakeResponse()

    def said(self):
        parts = []
        for message in self.response.messages:
            if message["content"]:
                parts.append(message["content"])
            if message["embed"] is not None:
                parts.append((message["embed"].title or "") + "\n"
                             + (message["embed"].description or ""))
        return "\n".join(parts)


@pytest.fixture
def cog():
    instance = object.__new__(SocialCreditCog)
    instance._processed_interactions = set()
    instance._max_tracked_interactions = 1000
    instance.supreme_smoke = {
        "target_id": None, "target_name": None,
        "leader_id": None, "votes": set(), "expires": None,
    }
    return instance


def call(command, cog, interaction, *args, **kwargs):
    return asyncio.run(command.callback(cog, interaction, *args, **kwargs))


# ----------------------------------------------------------------- /thank

def test_thank_awards_the_helper(cog, db):
    helper = FakeMember(1, "alice")
    interaction = FakeInteraction(user=FakeMember(2, "bob"))

    call(SocialCreditCog.thank, cog, interaction, helper, "fixing the drivetrain")

    assert db.get_user("1")["total_credits"] == config.CREDITS["helping_others"]
    assert "fixing the drivetrain" in db.get_transactions("1")[0]["reason"]


def test_you_cannot_thank_yourself(cog, db):
    person = FakeMember(1, "alice")
    interaction = FakeInteraction(user=person)

    call(SocialCreditCog.thank, cog, interaction, person, "being great")

    assert "can't thank yourself" in interaction.said()
    assert db.get_user("1") is None


def test_you_cannot_thank_a_bot(cog, db):
    interaction = FakeInteraction(user=FakeMember(2, "bob"))

    call(SocialCreditCog.thank, cog, interaction, FakeMember(1, "botty", bot=True), "x")

    assert "bot" in interaction.said().lower()
    assert db.get_user("1") is None


def test_thanking_twice_pays_twice(cog, db):
    """Distinct interactions are distinct thanks."""
    helper = FakeMember(1, "alice")

    call(SocialCreditCog.thank, cog, FakeInteraction(1, FakeMember(2, "bob")), helper, "x")
    call(SocialCreditCog.thank, cog, FakeInteraction(2, FakeMember(3, "carol")), helper, "y")

    assert db.get_user("1")["total_credits"] == 2 * config.CREDITS["helping_others"]


# ----------------------------------------------------------- /magic-smoke

def _vote(cog, db, target, voter_id, interaction_id):
    interaction = FakeInteraction(interaction_id, FakeMember(voter_id, f"voter{voter_id}"))
    call(SocialCreditCog.magic_smoke, cog, interaction, target)
    return interaction


def test_a_single_vote_does_not_punish(cog, db):
    target = FakeMember(1, "bob")

    interaction = _vote(cog, db, target, 2, 1)

    assert f"1/{config.MAGIC_SMOKE_VOTES_REQUIRED}" in interaction.said()
    assert db.get_user("1") is None


def test_the_threshold_applies_the_penalty(cog, db):
    target = FakeMember(1, "bob")

    for i in range(config.MAGIC_SMOKE_VOTES_REQUIRED):
        last = _vote(cog, db, target, 10 + i, i + 1)

    assert db.get_user("1")["total_credits"] == config.CREDITS["magic_smoke"]
    assert "MAGIC SMOKE RELEASED" in last.said()


def test_you_cannot_smoke_yourself(cog, db):
    person = FakeMember(1, "bob")
    interaction = FakeInteraction(user=person)

    call(SocialCreditCog.magic_smoke, cog, interaction, person)

    assert "can't vote against yourself" in interaction.said()


def test_you_cannot_smoke_a_bot(cog, db):
    interaction = FakeInteraction(user=FakeMember(2, "voter"))

    call(SocialCreditCog.magic_smoke, cog, interaction, FakeMember(1, "botty", bot=True))

    assert "bot" in interaction.said().lower()


def test_voting_twice_is_refused_and_shows_the_tally(cog, db):
    target = FakeMember(1, "bob")
    _vote(cog, db, target, 2, 1)

    second = _vote(cog, db, target, 2, 2)

    assert "already voted" in second.said()
    assert db.get_user("1") is None


def test_a_person_can_be_smoked_twice(cog, db):
    """Regression: the second round used to raise and lock the database."""
    target = FakeMember(1, "bob")
    interaction_id = 0

    for _round in range(2):
        for i in range(config.MAGIC_SMOKE_VOTES_REQUIRED):
            interaction_id += 1
            _vote(cog, db, target, 10 + i, interaction_id)

    assert db.get_user("1")["total_credits"] == 2 * config.CREDITS["magic_smoke"]


def test_the_bot_keeps_working_after_a_second_smoke(cog, db):
    """Regression: a stranded write transaction locked out everything else."""
    target = FakeMember(1, "bob")
    interaction_id = 0
    for _round in range(2):
        for i in range(config.MAGIC_SMOKE_VOTES_REQUIRED):
            interaction_id += 1
            _vote(cog, db, target, 10 + i, interaction_id)

    db.start_checkin("9", "frank", "msg")
    assert db.get_active_checkin("9") is not None


# --------------------------------------------------------- /supreme-smoke

def test_only_the_supreme_leader_can_nominate(cog, db):
    interaction = FakeInteraction(user=FakeMember(100, "nobody"))

    call(SocialCreditCog.supreme_smoke, cog, interaction, FakeMember(1, "bob"))

    assert "Only the" in interaction.said()
    assert cog.supreme_smoke["target_id"] is None


def test_the_leader_can_nominate(cog, db):
    interaction = FakeInteraction(user=leader())

    call(SocialCreditCog.supreme_smoke, cog, interaction, FakeMember(1, "bob"))

    assert cog.supreme_smoke["target_id"] == "1"
    assert cog.supreme_smoke["leader_id"] == "100"
    assert "SUPREME LEADER" in interaction.said()


def test_the_leader_cannot_nominate_themselves(cog, db):
    chief = leader()
    interaction = FakeInteraction(user=chief)

    call(SocialCreditCog.supreme_smoke, cog, interaction, chief)

    assert "can't nominate yourself" in interaction.said()
    assert cog.supreme_smoke["target_id"] is None


def test_the_leader_cannot_nominate_a_bot(cog, db):
    interaction = FakeInteraction(user=leader())

    call(SocialCreditCog.supreme_smoke, cog, interaction,
         FakeMember(1, "botty", bot=True))

    assert "bot" in interaction.said().lower()
    assert cog.supreme_smoke["target_id"] is None


# ----------------------------------------------------------- /agree-smoke

def _nominate(cog, target_id=1, leader_id=100):
    cog.supreme_smoke = {
        "target_id": str(target_id), "target_name": "bob",
        "leader_id": str(leader_id), "votes": set(),
        "expires": datetime.now() + timedelta(hours=24),
    }


def test_agreeing_without_a_nomination(cog, db):
    interaction = FakeInteraction(user=FakeMember(2, "voter"))

    call(SocialCreditCog.agree_smoke, cog, interaction)

    assert "No active" in interaction.said()


def test_an_expired_nomination_is_cleared(cog, db):
    _nominate(cog)
    cog.supreme_smoke["expires"] = datetime.now() - timedelta(minutes=1)
    interaction = FakeInteraction(user=FakeMember(2, "voter"))

    call(SocialCreditCog.agree_smoke, cog, interaction)

    assert "expired" in interaction.said()
    assert cog.supreme_smoke["target_id"] is None


def test_the_leader_cannot_pad_their_own_nomination(cog, db):
    _nominate(cog)
    interaction = FakeInteraction(user=leader())

    call(SocialCreditCog.agree_smoke, cog, interaction)

    assert "already counts" in interaction.said()
    assert cog.supreme_smoke["votes"] == set()


def test_the_target_cannot_vote_on_their_own_punishment(cog, db):
    _nominate(cog, target_id=1)
    interaction = FakeInteraction(user=FakeMember(1, "bob"))

    call(SocialCreditCog.agree_smoke, cog, interaction)

    assert "your own punishment" in interaction.said()
    assert cog.supreme_smoke["votes"] == set()


def test_one_supporter_is_not_enough(cog, db):
    _nominate(cog)
    interaction = FakeInteraction(user=FakeMember(2, "voter"))

    call(SocialCreditCog.agree_smoke, cog, interaction)

    assert "1/2" in interaction.said()
    assert db.get_user("1") is None


def test_two_supporters_execute_the_judgment(cog, db):
    _nominate(cog)

    call(SocialCreditCog.agree_smoke, cog, FakeInteraction(1, FakeMember(2, "x")))
    last = FakeInteraction(2, FakeMember(3, "y"))
    call(SocialCreditCog.agree_smoke, cog, last)

    assert db.get_user("1")["total_credits"] == config.CREDITS["magic_smoke"]
    assert "SUPREME JUDGMENT EXECUTED" in last.said()
    assert cog.supreme_smoke["target_id"] is None  # reset for the next one


def test_the_same_supporter_cannot_count_twice(cog, db):
    _nominate(cog)
    supporter = FakeMember(2, "voter")

    call(SocialCreditCog.agree_smoke, cog, FakeInteraction(1, supporter))
    call(SocialCreditCog.agree_smoke, cog, FakeInteraction(2, supporter))

    assert cog.supreme_smoke["votes"] == {"2"}
    assert db.get_user("1") is None
