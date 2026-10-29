"""Tests for Context Bot (contextbot/).

Claude and Discord are both faked: a stand-in Anthropic client records the
requests it is sent, and the cog is driven through plain objects shaped like
discord.py's. The cog's scheduler loop is not started.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import anthropic
import discord
import httpx2
import pytest
from anthropic.types.beta import BetaTextBlock, BetaThinkingBlock

from contextbot import brain as brain_mod
from contextbot import config, store
from contextbot.brain import (
    Answer, Brain, BrainError, ChatSession, build_context, _history_blocks,
)
from contextbot.cog import ContextCog, due_jobs, footer, split_message

CEO, ALICE, BOB, CAROL = 100, 1, 2, 3
GUILD = 555


@pytest.fixture(autouse=True)
def cdb(tmp_path, monkeypatch):
    """A throwaway Context Bot database, and settings no .env can change."""
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "contextbot.db"))
    monkeypatch.setattr(config, "GUILD_ID", GUILD)
    monkeypatch.setattr(config, "OWNER_IDS", set())
    monkeypatch.setattr(config, "CHANNEL_IDS", set())
    monkeypatch.setattr(config, "REPORT_CHANNEL_ID", 0)
    monkeypatch.setattr(config, "MODEL", "claude-opus-5-5")
    monkeypatch.setattr(config, "EFFORT", "high")
    monkeypatch.setattr(config, "CHAT_EFFORT", "medium")
    monkeypatch.setattr(config, "DAILY_HOUR", 8)
    monkeypatch.setattr(config, "WEEKLY_DAY", "friday")
    monkeypatch.setattr(config, "WEEKLY_HOUR", 17)
    store.init()
    return store


def ago(**kw) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


def log(message_id, author_id, name, text, when, kind="chat", channel="eng"):
    store.log_message(message_id, 10, channel, author_id, name, text, when, kind)


# ==================================================================== store

def test_messages_come_back_oldest_first_and_backfill_does_not_duplicate():
    log("b", ALICE, "Alice", "second", ago(hours=1))
    log("a", ALICE, "Alice", "first", ago(hours=2))
    assert store.log_message("a", 10, "eng", ALICE, "Alice", "again", ago(hours=2)) is False

    rows = store.messages_since(ago(days=1))
    assert [r["content"] for r in rows] == ["first", "second"]


def test_window_and_author_filter():
    log("old", ALICE, "Alice", "last month", ago(days=30))
    log("new", ALICE, "Alice", "today", ago(hours=1))
    log("bob", BOB, "Bob", "hi", ago(hours=1))

    assert [r["message_id"] for r in store.messages_since(ago(days=7))] == ["bob", "new"]
    assert [r["message_id"] for r in store.messages_since(ago(days=7), author_id=ALICE)] == ["new"]


def test_edits_only_count_when_the_text_changes():
    log("m", ALICE, "Alice", "draft", ago(hours=1))
    assert store.edit_message("m", "draft") is False  # a link unfurl, not an edit
    assert store.edit_message("m", "final") is True
    row = store.messages_since(ago(days=1))[0]
    assert (row["content"], row["edited"]) == ("final", 1)


def test_deleting_and_forgetting():
    log("m1", ALICE, "Alice", "one", ago(hours=1))
    log("m2", ALICE, "Alice", "two", ago(hours=1))
    log("m3", BOB, "Bob", "three", ago(hours=1))

    assert store.delete_message("m1") is True
    assert store.forget_author(ALICE) == 1
    assert [r["message_id"] for r in store.messages_since(ago(days=1))] == ["m3"]


def test_prune_drops_only_old_messages():
    log("old", ALICE, "Alice", "ancient", ago(days=100))
    log("new", ALICE, "Alice", "fresh", ago(days=1))
    assert store.prune(90) == 1
    assert [r["message_id"] for r in store.messages_since(ago(days=365))] == ["new"]


def test_last_report_is_per_kind_and_per_member():
    store.save_report("scorecard", "week 1")
    store.save_report("scorecard", "week 2")
    store.save_report("judge", "alice review", subject_id=ALICE)

    assert store.last_report("scorecard")["text"] == "week 2"
    assert store.last_report("judge", subject_id=ALICE)["text"] == "alice review"
    assert store.last_report("judge", subject_id=BOB) is None
    assert store.last_report("judge") is None  # a member's review isn't the team's


def test_settings_round_trip_and_clear():
    assert store.get_setting("brief") == ""
    store.set_setting("brief", "Ship v2 by December")
    assert store.get_setting("brief") == "Ship v2 by December"
    store.set_setting("brief", "")
    assert store.get_setting("brief") == ""


# ================================================================== context

def test_context_lists_quiet_members_tags_updates_and_the_ceo():
    log("1", ALICE, "Alice", "merged the parser\nand tests", ago(hours=3))
    log("2", ALICE, "Alice", "parser done, starting export", ago(hours=2), kind="update")
    log("3", CEO, "Ian", "great work", ago(hours=1))
    log("4", 77, "Dana", "bye all", ago(hours=1))   # has since left the server
    roster = {str(ALICE): "Alice", str(BOB): "Bob", str(CEO): "Ian"}

    text, dropped = build_context(store.messages_since(ago(days=1)), roster,
                                  ago(days=1), ago(), owner_ids={str(CEO)})

    assert dropped == 0
    assert "Alice: merged the parser\n    and tests" in text
    assert "Alice [UPDATE]: parser done" in text
    assert "- Alice: 1 message, 1 update" in text
    assert "- Bob: no visible activity" in text
    assert "- Ian (CEO): 1 message" in text
    assert "- Dana (no longer in the server): 1 message" in text


def test_context_leaves_out_the_oldest_messages_and_says_so():
    for i in range(50):
        log(f"m{i:02}", ALICE, "Alice", f"message number {i:02} " + "x" * 80,
            ago(minutes=100 - i))
    rows = store.messages_since(ago(days=1))

    text, dropped = build_context(rows, {}, ago(days=1), ago(), set(), max_chars=2000)

    assert dropped > 0
    assert f"The {dropped} oldest messages" in text
    assert "message number 49" in text            # newest kept
    assert "message number 00" not in text        # oldest gone


def test_footer_mentions_dropped_messages():
    assert footer(7, 120, 0) == "-# Last 7 days · 120 messages read"
    assert "the 5 oldest didn't fit" in footer(1, 9, 5)
    assert footer(1, 9, 0).startswith("-# Last 1 day ·")


# ================================================================ splitting

def test_split_message_respects_the_limit_and_keeps_everything():
    text = "\n".join(f"line {i} " + "y" * 50 for i in range(200))
    chunks = split_message(text, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_message_hard_splits_an_enormous_line():
    chunks = split_message("z" * 1200, limit=500)
    assert [len(c) for c in chunks] == [500, 500, 200]


# ================================================================ schedule

FRIDAY = datetime(2026, 10, 30)  # a Friday


def test_daily_brief_runs_once_in_its_window():
    at = lambda h: FRIDAY.replace(hour=h)
    assert "daily" not in due_jobs(at(7), {})
    assert "daily" in due_jobs(at(8), {})
    assert "daily" in due_jobs(at(10), {})          # caught up after a restart
    assert "daily" not in due_jobs(at(11), {})      # too late to be a morning brief
    assert "daily" not in due_jobs(at(9), {"daily": FRIDAY.date().isoformat()})


def test_weekly_runs_only_on_its_day(monkeypatch):
    assert "weekly" in due_jobs(FRIDAY.replace(hour=17), {})
    assert "weekly" not in due_jobs((FRIDAY - timedelta(days=1)).replace(hour=17), {})
    monkeypatch.setattr(config, "WEEKLY_HOUR", -1)
    assert "weekly" not in due_jobs(FRIDAY.replace(hour=17), {})


def test_reports_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "DAILY_HOUR", -1)
    assert due_jobs(FRIDAY.replace(hour=0), {}) == ["prune"]


# =================================================================== Claude

def final(text="All good.", stop="end_turn", content=None, model="claude-opus-5-5"):
    return SimpleNamespace(
        content=content or [BetaThinkingBlock(type="thinking", thinking="", signature="sig"),
                            BetaTextBlock(type="text", text=text)],
        stop_reason=stop, stop_details=None, model=model,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeStream:
    def __init__(self, message):
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self.message


class FakeClient:
    """Records every request; answers with the queued responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses) or [final()]
        self.calls = []
        self.beta = SimpleNamespace(messages=self)

    def stream(self, **request):
        self.calls.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return FakeStream(response)


def api_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("nope", response=httpx2.Response(status, request=request), body=None)


SYSTEM = [{"type": "text", "text": "system"}]


def test_report_request_caches_the_transcript_and_opts_into_fallbacks():
    client = FakeClient(final("Report text"))
    answer = asyncio.run(Brain(client).run(SYSTEM, "TRANSCRIPT", "TASK"))

    assert answer.text == "Report text"
    request = client.calls[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"] == {"effort": "high"}
    assert request["betas"] == [brain_mod.FALLBACK_BETA]
    assert request["fallbacks"] == "default"
    assert "thinking" not in request and "temperature" not in request
    assert "cache_control" not in request  # a one-shot caches the transcript only
    context, task = request["messages"][0]["content"]
    assert context == {"type": "text", "text": "TRANSCRIPT",
                       "cache_control": {"type": "ephemeral"}}
    assert task == {"type": "text", "text": "TASK"}


def test_models_without_server_side_fallback_are_not_sent_it(monkeypatch):
    monkeypatch.setattr(config, "MODEL", "claude-haiku-5-5")
    client = FakeClient()
    asyncio.run(Brain(client).run(SYSTEM, "T", "Q"))
    assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]


def test_chat_keeps_an_append_only_history_and_caches_its_tail():
    client = FakeClient(final("First answer"), final("Second answer"))
    brain = Brain(client)
    now = datetime.now()
    session = ChatSession(context="TRANSCRIPT", started=now, last_active=now)

    asyncio.run(brain.chat(session, SYSTEM, "Who is blocked?"))
    asyncio.run(brain.chat(session, SYSTEM, "And Bob?"))

    first, second = client.calls
    assert first["output_config"] == {"effort": "medium"}
    assert first["cache_control"] == {"type": "ephemeral"}
    opener = first["messages"][0]["content"]
    assert opener[0]["cache_control"] == {"type": "ephemeral"}
    assert opener[1]["text"].endswith("Who is blocked?")
    # The second request starts with the first one, byte for byte
    assert second["messages"][:1] == first["messages"]
    assert second["messages"][1]["role"] == "assistant"
    assert second["messages"][1]["content"][0]["type"] == "thinking"  # sent back unchanged
    assert second["messages"][2] == {"role": "user", "content": "And Bob?"}
    assert session.turns == 2


def test_a_failed_chat_turn_leaves_the_conversation_untouched():
    client = FakeClient(api_error(anthropic.RateLimitError, 429))
    now = datetime.now()
    session = ChatSession(context="T", started=now, last_active=now)
    with pytest.raises(BrainError, match="rate-limiting"):
        asyncio.run(Brain(client).chat(session, SYSTEM, "hello"))
    assert session.messages == []


@pytest.mark.parametrize("error, says", [
    (api_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY"),
    (api_error(anthropic.NotFoundError, 404), "CONTEXTBOT_MODEL"),
    (api_error(anthropic.InternalServerError, 500), "on its end"),
    (api_error(anthropic.RequestTooLargeError, 413), "fewer days"),
    (anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com")), "Couldn't reach"),
])
def test_api_errors_become_messages_the_ceo_can_act_on(error, says):
    with pytest.raises(BrainError, match=says):
        asyncio.run(Brain(FakeClient(error)).run(SYSTEM, "T", "Q"))


def test_a_missing_api_key_says_which_setting_to_fix(monkeypatch):
    # The SDK raises a bare TypeError when it finds no credentials at all
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    client = FakeClient(TypeError("Could not resolve authentication method."))
    with pytest.raises(BrainError, match="ANTHROPIC_API_KEY"):
        asyncio.run(Brain(client).run(SYSTEM, "T", "Q"))


def test_other_type_errors_are_not_disguised_as_a_missing_key(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    with pytest.raises(TypeError):
        asyncio.run(Brain(FakeClient(TypeError("a real bug"))).run(SYSTEM, "T", "Q"))


def test_a_refusal_is_reported_not_posted():
    with pytest.raises(BrainError, match="declined"):
        asyncio.run(Brain(FakeClient(final("", stop="refusal"))).run(SYSTEM, "T", "Q"))


def test_a_cut_off_answer_says_so():
    answer = asyncio.run(Brain(FakeClient(final("Partial", stop="max_tokens"))).run(SYSTEM, "T", "Q"))
    assert answer.text.startswith("Partial") and "length limit" in answer.text


def test_history_drops_the_declined_part_of_a_fallback_answer():
    content = [
        {"type": "thinking", "thinking": "", "signature": "a"},
        {"type": "text", "text": "Part one. "},
        {"type": "fallback", "from": {"model": "x"}, "to": {"model": "y"}},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "Part two."},
    ]
    assert _history_blocks(content) == [
        {"type": "text", "text": "Part one. "},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "Part two."},
    ]


def test_system_prompt_names_the_ceo_and_carries_the_brief():
    blocks = Brain(FakeClient()).system(["Ian"], "Goal: ship v2")
    assert config.ORG_NAME in blocks[0]["text"]
    assert "The CEO: Ian" in blocks[1]["text"]
    assert "<brief>\nGoal: ship v2\n</brief>" in blocks[1]["text"]
    assert "/brief" in Brain(FakeClient()).system(["Ian"], "")[1]["text"]


# ===================================================================== cog

class FakeMember:
    def __init__(self, member_id, name, bot=False):
        self.id = member_id
        self.display_name = name
        self.name = name.lower()
        self.bot = bot
        self.dms = []

    async def send(self, content=None, file=None):
        self.dms.append(content)


class FakeGuild:
    def __init__(self, members, owner_id=CEO, guild_id=GUILD):
        self.id = guild_id
        self.name = "EXP"
        self.owner_id = owner_id
        self.members = members

    def get_member(self, member_id):
        return next((m for m in self.members if m.id == member_id), None)


class FakeBot:
    def __init__(self, guilds):
        self.guilds = guilds

    def get_guild(self, guild_id):
        return next((g for g in self.guilds if g.id == guild_id), None)

    def get_user(self, user_id):
        for guild in self.guilds:
            member = guild.get_member(user_id)
            if member:
                return member
        return None


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeChannel:
    def __init__(self, channel_id=10, name="eng", parent=None):
        self.id = channel_id
        self.name = name
        self.parent = parent
        self.parent_id = parent.id if parent else None
        self.sent = []

    async def send(self, content=None, file=None):
        self.sent.append(content)

    def typing(self):
        return Typing()


def fake_message(author, text, guild=None, channel=None, message_id=1,
                 kind=discord.MessageType.default):
    return SimpleNamespace(
        id=message_id, author=author, guild=guild, content=text, clean_content=text,
        attachments=[], type=kind, channel=channel or FakeChannel(),
        created_at=datetime.now(timezone.utc))


class FakeBrain(Brain):
    """Real prompt-building, canned answers."""

    def __init__(self, reply="Here is what I see.", error=None):
        super().__init__(client=FakeClient())
        self.reply, self.error = reply, error
        self.runs, self.chats = [], []

    async def run(self, system, context, task, effort=None):
        if self.error:
            raise self.error
        self.runs.append({"system": system, "context": context, "task": task})
        return Answer(text=self.reply, content=[], model="claude-opus-5-5")

    async def chat(self, session, system, text):
        if self.error:
            raise self.error
        self.chats.append((session, text))
        session.messages += [{"role": "user", "content": text},
                             {"role": "assistant", "content": []}]
        return Answer(text=self.reply, content=[], model="claude-opus-5-5")


class Response:
    def __init__(self):
        self.messages, self.deferred = [], None
        self._done = False

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))
        self._done = True

    async def defer(self, ephemeral=False, thinking=False):
        self.deferred = ephemeral
        self._done = True

    def is_done(self):
        return self._done


class Followup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, ephemeral=False, file=None):
        self.sent.append((content, ephemeral))


def fake_interaction(user, guild):
    return SimpleNamespace(
        user=user, guild=guild, id=4242, channel_id=10, channel=FakeChannel(),
        created_at=datetime.now(timezone.utc), response=Response(), followup=Followup())


@pytest.fixture
def team():
    return {
        "ceo": FakeMember(CEO, "Ian"),
        "alice": FakeMember(ALICE, "Alice"),
        "bob": FakeMember(BOB, "Bob"),
        "bot": FakeMember(999, "Context Bot", bot=True),
    }


@pytest.fixture
def guild(team):
    return FakeGuild(list(team.values()))


def make_cog(guild, brain=None, other_guilds=()):
    cog = object.__new__(ContextCog)
    cog.bot = FakeBot([guild, *other_guilds])
    cog.brain = brain or FakeBrain()
    cog.sessions, cog.locks = {}, {}
    return cog


def test_the_server_owner_is_the_ceo_unless_configured(guild, team, monkeypatch):
    cog = make_cog(guild)
    assert cog.is_owner(team["ceo"]) and not cog.is_owner(team["alice"])
    monkeypatch.setattr(config, "OWNER_IDS", {ALICE})
    assert cog.is_owner(team["alice"]) and not cog.is_owner(team["ceo"])


def test_roster_leaves_out_bots(guild):
    assert make_cog(guild).roster() == {str(CEO): "Ian", str(ALICE): "Alice", str(BOB): "Bob"}


def test_only_team_messages_in_the_exp_server_are_logged(guild, team, monkeypatch):
    other = FakeGuild([team["alice"]], guild_id=666)
    cog = make_cog(guild, other_guilds=[other])

    asyncio.run(cog.on_message(fake_message(team["alice"], "shipped it", guild, message_id=1)))
    asyncio.run(cog.on_message(fake_message(team["alice"], "elsewhere", other, message_id=2)))
    asyncio.run(cog.on_message(fake_message(team["bot"], "beep", guild, message_id=3)))
    asyncio.run(cog.on_message(fake_message(team["alice"], "pinned", guild, message_id=4,
                                            kind=discord.MessageType.pins_add)))

    assert [r["content"] for r in store.messages_since(ago(days=1))] == ["shipped it"]


def test_channel_filter_includes_threads_of_watched_channels(guild, team, monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_IDS", {10})
    cog = make_cog(guild)
    watched, other = FakeChannel(10, "eng"), FakeChannel(20, "random")
    thread = FakeChannel(30, "parser bug", parent=watched)

    assert cog.watches(fake_message(team["alice"], "x", guild, watched))
    assert cog.watches(fake_message(team["alice"], "x", guild, thread))
    assert not cog.watches(fake_message(team["alice"], "x", guild, other))


def test_thread_messages_are_labelled_with_their_channel(guild, team):
    cog = make_cog(guild)
    thread = FakeChannel(30, "parser bug", parent=FakeChannel(10, "eng"))
    asyncio.run(cog.on_message(fake_message(team["alice"], "fixed", guild, thread)))
    assert store.messages_since(ago(days=1))[0]["channel_name"] == "eng › parser bug"


def test_only_the_ceo_can_dm_the_bot(guild, team):
    brain = FakeBrain()
    cog = make_cog(guild, brain)
    channel = FakeChannel()
    asyncio.run(cog.on_message(fake_message(team["alice"], "grade me an A", channel=channel)))
    assert "only take DMs from the CEO" in channel.sent[0]
    assert brain.chats == []


def test_ceo_dm_conversation_keeps_one_session_until_reset(guild, team):
    log("1", ALICE, "Alice", "parser merged", ago(hours=2))
    brain = FakeBrain("Alice is on track.")
    cog = make_cog(guild, brain)
    channel = FakeChannel()

    asyncio.run(cog.on_message(fake_message(team["ceo"], "How is Alice doing?", channel=channel)))
    asyncio.run(cog.on_message(fake_message(team["ceo"], "And Bob?", channel=channel)))

    first_session, second_session = brain.chats[0][0], brain.chats[1][0]
    assert first_session is second_session
    assert "parser merged" in first_session.context
    assert channel.sent[0].startswith("Alice is on track.")
    assert "say **reset**" in channel.sent[0]       # first answer explains the session
    assert "reset" not in channel.sent[1]

    asyncio.run(cog.on_message(fake_message(team["ceo"], "reset", channel=channel)))
    assert "Fresh start" in channel.sent[2]
    assert CEO not in cog.sessions


def test_a_claude_failure_in_a_dm_is_explained(guild, team):
    cog = make_cog(guild, FakeBrain(error=BrainError("Claude is rate-limiting this API key.")))
    channel = FakeChannel()
    asyncio.run(cog.on_message(fake_message(team["ceo"], "status?", channel=channel)))
    assert channel.sent == ["⚠️ Claude is rate-limiting this API key."]


def test_report_is_ceo_only(guild, team):
    brain = FakeBrain()
    cog = make_cog(guild, brain)
    interaction = fake_interaction(team["alice"], guild)
    asyncio.run(cog.report.callback(cog, interaction, days=7, focus=None, public=False))
    assert "Only the CEO" in interaction.response.messages[0][0]
    assert brain.runs == []


def test_report_is_private_by_default_and_saved(guild, team):
    log("1", ALICE, "Alice", "parser merged", ago(hours=2))
    brain = FakeBrain("## TL;DR\n- Parser shipped")
    cog = make_cog(guild, brain)
    interaction = fake_interaction(team["ceo"], guild)

    asyncio.run(cog.report.callback(cog, interaction, days=7, focus="the parser", public=False))

    assert interaction.response.deferred is True     # ephemeral
    content, ephemeral = interaction.followup.sent[0]
    assert ephemeral and content.startswith("## TL;DR")
    assert "1 message read" in content
    assert "parser merged" in brain.runs[0]["context"]
    assert "focus on: the parser" in brain.runs[0]["task"]
    assert store.last_report("report")["text"] == "## TL;DR\n- Parser shipped"


def test_judging_the_team_compares_with_the_last_scorecard(guild, team):
    store.save_report("scorecard", "Alice — B")
    brain = FakeBrain("Alice — A")
    cog = make_cog(guild, brain)
    interaction = fake_interaction(team["ceo"], guild)

    asyncio.run(cog.judge.callback(cog, interaction, member=None, days=7))

    assert "<previous_scorecard>\nAlice — B" in brain.runs[0]["task"]
    assert interaction.followup.sent[0][1] is True   # always private
    assert store.last_report("scorecard")["text"] == "Alice — A"


def test_judging_one_member(guild, team):
    brain = FakeBrain("## Verdict\nB")
    cog = make_cog(guild, brain)
    asyncio.run(cog.judge.callback(cog, fake_interaction(team["ceo"], guild),
                                   member=team["bob"], days=14))
    assert "assessment of Bob's work over the last 14 days" in brain.runs[0]["task"]
    assert store.last_report("judge", subject_id=BOB)["text"] == "## Verdict\nB"


def test_anyone_can_log_an_update(guild, team):
    cog = make_cog(guild)
    interaction = fake_interaction(team["alice"], guild)
    asyncio.run(cog.update.callback(cog, interaction, text="Finished the export API"))

    row = store.messages_since(ago(days=1))[0]
    assert (row["kind"], row["author_name"], row["content"]) == (
        "update", "Alice", "Finished the export API")
    assert interaction.response.messages[0][1] is True


def test_brief_is_saved_and_shown(guild, team):
    cog = make_cog(guild)
    asyncio.run(cog.brief.callback(cog, fake_interaction(team["ceo"], guild),
                                   text="Q4: land 3 pilots", file=None))
    assert store.get_setting("brief") == "Q4: land 3 pilots"
    assert "<brief>\nQ4: land 3 pilots" in cog.system()[1]["text"]

    shown = fake_interaction(team["ceo"], guild)
    asyncio.run(cog.brief.callback(cog, shown, text=None, file=None))
    assert "Q4: land 3 pilots" in shown.followup.sent[0][0]


def test_daily_brief_is_dmd_to_the_ceo_once(guild, team):
    log("1", ALICE, "Alice", "parser merged", ago(hours=2))
    brain = FakeBrain("Parser merged; nothing blocked.")
    cog = make_cog(guild, brain)
    morning = datetime.now().replace(hour=8, minute=5)

    asyncio.run(cog.run_due_jobs(morning))
    asyncio.run(cog.run_due_jobs(morning + timedelta(minutes=5)))

    assert len(team["ceo"].dms) == 1
    assert team["ceo"].dms[0].startswith("☀️ **Morning brief**\nParser merged")
    assert team["alice"].dms == []


def test_a_scheduled_report_that_fails_tells_the_ceo_why(guild, team):
    log("1", ALICE, "Alice", "parser merged", ago(hours=2))
    cog = make_cog(guild, FakeBrain(error=BrainError("No Claude API key.")))
    asyncio.run(cog.run_due_jobs(datetime.now().replace(hour=8)))
    assert team["ceo"].dms == ["⚠️ Your morning brief didn't go out: No Claude API key."]


def test_a_quiet_day_sends_no_brief(guild, team):
    cog = make_cog(guild)
    asyncio.run(cog.run_due_jobs(datetime.now().replace(hour=8)))
    assert team["ceo"].dms == []


def test_weekly_sends_the_report_and_scorecard_from_one_transcript(guild, team, monkeypatch):
    log("1", ALICE, "Alice", "parser merged", ago(days=2))
    brain = FakeBrain("Solid week.")
    cog = make_cog(guild, brain)
    monkeypatch.setattr(config, "DAILY_HOUR", -1)
    friday = datetime.now()
    monkeypatch.setattr(config, "WEEKLY_DAY", ["monday", "tuesday", "wednesday", "thursday",
                                               "friday", "saturday", "sunday"][friday.weekday()])
    friday = friday.replace(hour=17)

    asyncio.run(cog.run_due_jobs(friday))

    assert [r["task"].split("\n")[0][:30] for r in brain.runs] == [
        "Write a progress report on the", "Judge each team member on the "]
    assert brain.runs[0]["context"] == brain.runs[1]["context"]
    assert team["ceo"].dms[0].startswith("📊 **Weekly report**")
    assert team["ceo"].dms[1].startswith("⚖️ **Team scorecard**")


def test_very_long_answers_go_out_as_a_file(guild):
    cog = make_cog(guild)
    sent = []

    async def send(content=None, file=None):
        sent.append((content, file))

    asyncio.run(cog._deliver(send, "\n".join("w" * 1500 for _ in range(20))))
    assert len(sent) == 1 and sent[0][1] is not None
    assert "full text attached" in sent[0][0]
