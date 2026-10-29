"""Context Bot's Discord behaviour: what it logs, its commands, its schedule.

Everything that judges people is CEO-only and private — ephemeral replies or
DMs — unless the CEO explicitly asks to post a progress report publicly.
"""
import asyncio
import functools
import io
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import config, store
from .brain import (
    Brain, BrainError, ChatSession, build_context, daily_task, member_task,
    plural, report_task, scorecard_task, CHAT_OPENER,
)

DISCORD_LIMIT = 1990          # Discord allows 2000 characters a message
MAX_CHUNKS = 8                # longer than this goes out as a file
CHAT_DAYS = 14                # how far back a DM conversation looks
CHAT_IDLE_MINUTES = 45        # a DM after this long starts a fresh look
CHAT_MAX_TURNS = 40
BACKFILL_PER_CHANNEL = 5000
CATCH_UP_HOURS = 3            # a report missed by a restart still goes out this late
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]
RESET_WORDS = {"reset", "new", "fresh", "start over", "refresh"}


# ------------------------------------------------------------------ helpers

def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split on line breaks into chunks Discord will accept."""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return [c for c in chunks if c.strip()] or [""]


def due_jobs(now: datetime, last_runs: dict) -> list[str]:
    """Which scheduled jobs should run at local time `now`.

    `last_runs` maps job name to the date (ISO) it last ran, so each job runs
    at most once a day and a restart neither repeats nor skips one.
    """
    today = now.date().isoformat()

    def due(hour: int) -> bool:
        return 0 <= hour <= now.hour < hour + CATCH_UP_HOURS

    jobs = []
    if last_runs.get("prune") != today:
        jobs.append("prune")
    if due(config.DAILY_HOUR) and last_runs.get("daily") != today:
        jobs.append("daily")
    if (config.WEEKLY_DAY in WEEKDAYS
            and now.weekday() == WEEKDAYS.index(config.WEEKLY_DAY)
            and due(config.WEEKLY_HOUR) and last_runs.get("weekly") != today):
        jobs.append("weekly")
    return jobs


def message_text(message: discord.Message) -> str:
    """The message as a person would read it: mentions as names, files named."""
    text = message.clean_content.strip()
    files = [a.filename for a in message.attachments]
    if files:
        text += ("\n" if text else "") + "[attached: " + ", ".join(files) + "]"
    return text


def channel_label(channel) -> str:
    parent = getattr(channel, "parent", None)
    name = getattr(channel, "name", None) or "dm"
    return f"{parent.name} › {name}" if parent is not None else name


def footer(days: int, count: int, dropped: int) -> str:
    text = f"-# Last {plural(days, 'day')} · {plural(count, 'message')} read"
    if dropped:
        text += f" · the {dropped} oldest didn't fit"
    return text


class ContextCog(commands.Cog):
    def __init__(self, bot: commands.Bot, brain: Optional[Brain] = None):
        self.bot = bot
        self.brain = brain or Brain()
        self.sessions: dict[int, ChatSession] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.scheduler.start()

    def cog_unload(self):
        self.scheduler.cancel()

    # ----------------------------------------------------------- who's who

    def guild(self) -> Optional[discord.Guild]:
        if config.GUILD_ID:
            return self.bot.get_guild(config.GUILD_ID)
        guilds = self.bot.guilds
        return guilds[0] if len(guilds) == 1 else None

    def owner_ids(self) -> set[int]:
        if config.OWNER_IDS:
            return set(config.OWNER_IDS)
        guild = self.guild()
        return {guild.owner_id} if guild and guild.owner_id else set()

    def is_owner(self, user) -> bool:
        return user.id in self.owner_ids()

    def owner_names(self) -> list[str]:
        guild = self.guild()
        names = []
        for owner_id in sorted(self.owner_ids()):
            member = guild.get_member(owner_id) if guild else None
            user = member or self.bot.get_user(owner_id)
            names.append(getattr(user, "display_name", None) or str(owner_id))
        return names

    def roster(self) -> dict[str, str]:
        guild = self.guild()
        if guild is None:
            return {}
        return {str(m.id): m.display_name for m in guild.members if not m.bot}

    def watches(self, message: discord.Message) -> bool:
        """Is this a team message in the EXP server worth remembering?"""
        if message.author.bot or message.guild is None:
            return False
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return False
        guild = self.guild()
        if guild is None or message.guild.id != guild.id:
            return False
        if config.CHANNEL_IDS:
            channel = message.channel
            here = {channel.id, getattr(channel, "parent_id", None)}
            if not here & config.CHANNEL_IDS:
                return False
        return True

    # ------------------------------------------------------------- context

    def system(self) -> list[dict]:
        return self.brain.system(self.owner_names(), store.get_setting("brief"))

    def context(self, since: datetime, until: datetime) -> tuple[str, int, int]:
        """The transcript Claude reads, how many messages it covers, and how
        many were left out to fit."""
        rows = store.messages_since(since)
        text, dropped = build_context(
            rows, self.roster(), since, until,
            owner_ids={str(i) for i in self.owner_ids()})
        return text, len(rows), dropped

    async def _deny_unless_owner(self, interaction: discord.Interaction) -> bool:
        if self.is_owner(interaction.user):
            return False
        await interaction.response.send_message(
            f"Only the CEO of {config.ORG_NAME} can use this.", ephemeral=True)
        return True

    async def _deliver(self, send, text: str):
        """Send a long answer as several messages, or as a file if very long."""
        chunks = split_message(text)
        if len(chunks) <= MAX_CHUNKS:
            for chunk in chunks:
                await send(content=chunk)
            return
        await send(content=chunks[0] + "\n\n*(Too long for Discord — full text attached.)*",
                   file=discord.File(io.BytesIO(text.encode("utf-8")), filename="report.md"))

    # ----------------------------------------------------------- listening

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild is None:
            await self.handle_dm(message)
            return
        if not self.watches(message):
            return
        text = message_text(message)
        if text:
            store.log_message(
                message.id, message.channel.id, channel_label(message.channel),
                message.author.id, message.author.display_name, text,
                message.created_at)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        message = getattr(payload, "message", None)
        if message is not None and self.watches(message):
            store.edit_message(message.id, message_text(message))

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        store.delete_message(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        for message_id in payload.message_ids:
            store.delete_message(message_id)

    # ---------------------------------------------------------- DM advisor

    def _session_for(self, user_id: int, now: datetime) -> tuple[ChatSession, bool]:
        session = self.sessions.get(user_id)
        if (session is not None
                and now - session.last_active < timedelta(minutes=CHAT_IDLE_MINUTES)
                and session.turns < CHAT_MAX_TURNS):
            return session, False
        since = now - timedelta(days=CHAT_DAYS)
        context, _, _ = self.context(since, now)
        session = ChatSession(context=context, started=now, last_active=now)
        self.sessions[user_id] = session
        return session, True

    async def handle_dm(self, message: discord.Message):
        if not self.is_owner(message.author):
            await message.channel.send(
                f"I only take DMs from the CEO of {config.ORG_NAME}. "
                f"To log your progress, use `/update` in the server.")
            return
        text = message.content.strip()
        if not text:
            return
        if text.lower().strip("!/. ") in RESET_WORDS:
            self.sessions.pop(message.author.id, None)
            await message.channel.send(
                "Fresh start. I'll pull the latest team activity with your next message.")
            return

        lock = self.locks.setdefault(message.author.id, asyncio.Lock())
        async with lock:
            now = datetime.now().astimezone()
            async with message.channel.typing():
                session, fresh = self._session_for(message.author.id, now)
                try:
                    answer = await self.brain.chat(session, self.system(), text)
                except BrainError as e:
                    await message.channel.send(f"⚠️ {e}")
                    return
            session.last_active = datetime.now().astimezone()
            reply = answer.text
            if fresh:
                reply += (f"\n-# Reading the last {CHAT_DAYS} days of team activity"
                          f" · say **reset** for a fresh look")
            await self._deliver(message.channel.send, reply)

    # ------------------------------------------------------------ commands

    @app_commands.command(name="report", description="Progress report on the team (CEO only)")
    @app_commands.describe(
        days="How many days back to look (default 7)",
        focus="Anything in particular to dig into",
        public="Post it in this channel for everyone to see (default: only you)")
    @app_commands.default_permissions(manage_guild=True)
    async def report(self, interaction: discord.Interaction,
                     days: app_commands.Range[int, 1, 90] = 7,
                     focus: Optional[str] = None, public: bool = False):
        if await self._deny_unless_owner(interaction):
            return
        await interaction.response.defer(ephemeral=not public, thinking=True)
        now = datetime.now().astimezone()
        context, count, dropped = self.context(now - timedelta(days=days), now)
        try:
            answer = await self.brain.run(self.system(), context, report_task(days, focus or ""))
        except BrainError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return
        store.save_report("report", answer.text, days=days)
        send = functools.partial(interaction.followup.send, ephemeral=not public)
        await self._deliver(send, answer.text + "\n" + footer(days, count, dropped))

    @app_commands.command(name="judge", description="Honest assessment of the team or one member (CEO only, private)")
    @app_commands.describe(
        member="One person to assess in depth (leave empty for the whole team)",
        days="How many days back to look (default 7)")
    @app_commands.default_permissions(manage_guild=True)
    async def judge(self, interaction: discord.Interaction,
                    member: Optional[discord.Member] = None,
                    days: app_commands.Range[int, 1, 90] = 7):
        if await self._deny_unless_owner(interaction):
            return
        if member is not None and member.bot:
            await interaction.response.send_message("Bots aren't on the team.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        now = datetime.now().astimezone()
        context, count, dropped = self.context(now - timedelta(days=days), now)
        if member is None:
            kind, subject = "scorecard", None
            task = scorecard_task(days, store.last_report("scorecard"))
        else:
            kind, subject = "judge", str(member.id)
            task = member_task(member.display_name, days,
                               store.last_report("judge", subject_id=subject))
        try:
            answer = await self.brain.run(self.system(), context, task)
        except BrainError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return
        store.save_report(kind, answer.text, days=days, subject_id=subject)
        send = functools.partial(interaction.followup.send, ephemeral=True)
        await self._deliver(send, answer.text + "\n" + footer(days, count, dropped))

    @app_commands.command(name="ask", description="Ask your chief of staff anything about the team (CEO only, private)")
    @app_commands.describe(
        question="What do you want to know or decide?",
        days="How many days of activity to consider (default 14)")
    @app_commands.default_permissions(manage_guild=True)
    async def ask(self, interaction: discord.Interaction, question: str,
                  days: app_commands.Range[int, 1, 90] = 14):
        if await self._deny_unless_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        now = datetime.now().astimezone()
        context, count, dropped = self.context(now - timedelta(days=days), now)
        try:
            answer = await self.brain.run(self.system(), context, CHAT_OPENER + question,
                                          effort=config.CHAT_EFFORT)
        except BrainError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return
        send = functools.partial(interaction.followup.send, ephemeral=True)
        await self._deliver(send, f"> {question}\n\n{answer.text}\n"
                                  + footer(days, count, dropped))

    @app_commands.command(name="update", description="Log what you got done, what's next, or what's blocking you")
    @app_commands.describe(text="Your progress update")
    async def update(self, interaction: discord.Interaction,
                     text: app_commands.Range[str, 1, 4000]):
        guild = self.guild()
        if interaction.guild is None or guild is None or interaction.guild.id != guild.id:
            await interaction.response.send_message(
                f"Use this in the {config.ORG_NAME} server.", ephemeral=True)
            return
        store.log_message(
            f"update-{interaction.id}", interaction.channel_id,
            channel_label(interaction.channel), interaction.user.id,
            interaction.user.display_name, text.strip(),
            interaction.created_at, kind="update")
        await interaction.response.send_message(
            "📝 Logged. It goes into the CEO's next progress report.", ephemeral=True)

    @app_commands.command(name="brief", description="Tell Context Bot the company's goals, priorities and who owns what (CEO only)")
    @app_commands.describe(
        text="The brief. Leave empty to see the current one; 'clear' removes it",
        file="Or upload it as a .txt or .md file")
    @app_commands.default_permissions(manage_guild=True)
    async def brief(self, interaction: discord.Interaction,
                    text: Optional[str] = None,
                    file: Optional[discord.Attachment] = None):
        if await self._deny_unless_owner(interaction):
            return
        if file is not None:
            if file.size > 100_000:
                await interaction.response.send_message(
                    "That file is over 100 KB. Trim it to what matters most.", ephemeral=True)
                return
            text = (await file.read()).decode("utf-8", errors="replace")
        if text is None:
            current = store.get_setting("brief")
            if not current:
                await interaction.response.send_message(
                    "No brief yet. Set one with `/brief text:` (or upload a file): "
                    "the goals for this quarter, what matters most, and who owns "
                    "what. Reports and judgments are measured against it.",
                    ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            send = functools.partial(interaction.followup.send, ephemeral=True)
            await self._deliver(send, "**Current brief**\n" + current)
            return
        if text.strip().lower() == "clear":
            store.set_setting("brief", "")
            await interaction.response.send_message("Brief cleared.", ephemeral=True)
            return
        store.set_setting("brief", text.strip())
        self.sessions.clear()  # conversations should see the new brief
        await interaction.response.send_message(
            f"Brief saved ({len(text.strip()):,} characters). Every report and "
            f"judgment from now on is measured against it.", ephemeral=True)

    @app_commands.command(name="backfill", description="Read the server's recent history so reports start with context (CEO only)")
    @app_commands.describe(days="How many days back to read (default 14)")
    @app_commands.default_permissions(manage_guild=True)
    async def backfill(self, interaction: discord.Interaction,
                       days: app_commands.Range[int, 1, 90] = 14):
        if await self._deny_unless_owner(interaction):
            return
        guild = self.guild()
        if guild is None:
            await interaction.response.send_message(
                "I can't tell which server to read. Set CONTEXTBOT_GUILD_ID.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        since = datetime.now().astimezone() - timedelta(days=days)
        added, skipped, per_channel = 0, [], []
        for channel in [*guild.text_channels, *guild.threads]:
            if config.CHANNEL_IDS and not (
                    {channel.id, getattr(channel, "parent_id", None)} & config.CHANNEL_IDS):
                continue
            perms = channel.permissions_for(guild.me)
            if not (perms.view_channel and perms.read_message_history):
                skipped.append(channel_label(channel))
                continue
            count = 0
            try:
                async for message in channel.history(limit=BACKFILL_PER_CHANNEL,
                                                     after=since, oldest_first=True):
                    if not self.watches(message):
                        continue
                    text = message_text(message)
                    if text and store.log_message(
                            message.id, channel.id, channel_label(channel),
                            message.author.id, message.author.display_name, text,
                            message.created_at):
                        count += 1
            except discord.Forbidden:
                skipped.append(channel_label(channel))
                continue
            except discord.HTTPException as e:
                print(f"⚠️ Backfill of #{channel_label(channel)} stopped early: {e}")
            if count:
                per_channel.append((channel_label(channel), count))
            added += count

        lines = [f"📚 Read the last {days} days: **{added}** new messages logged."]
        for name, count in sorted(per_channel, key=lambda c: -c[1])[:15]:
            lines.append(f"- #{name}: {count}")
        if skipped:
            lines.append(f"Couldn't read: {', '.join('#' + s for s in skipped[:15])}"
                         " — give me *View Channel* and *Read Message History* there.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="status", description="What Context Bot has logged and how it's set up (CEO only)")
    @app_commands.default_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        if await self._deny_unless_owner(interaction):
            return
        s = store.stats()
        report_channel = (f"<#{config.REPORT_CHANNEL_ID}>" if config.REPORT_CHANNEL_ID
                          else "DMs only")
        daily = f"{config.DAILY_HOUR}:00" if 0 <= config.DAILY_HOUR <= 23 else "off"
        weekly = (f"{config.WEEKLY_DAY.title()} {config.WEEKLY_HOUR}:00"
                  if 0 <= config.WEEKLY_HOUR <= 23 else "off")
        lines = [
            f"**Context Bot** for {config.ORG_NAME}",
            f"- Logged: **{s['total']:,}** messages ({s['last_7_days']:,} in the last 7 days)",
            f"- Watching: {'selected channels' if config.CHANNEL_IDS else 'every channel I can read'}",
            f"- CEO: {', '.join(self.owner_names()) or 'unknown'}",
            f"- Model: `{config.MODEL}` (effort {config.EFFORT}, chat {config.CHAT_EFFORT})",
            f"- Claude API key: {'set' if config.ANTHROPIC_API_KEY else '**missing** — set ANTHROPIC_API_KEY'}",
            f"- Morning brief: {daily} · Weekly report + scorecard: {weekly} → {report_channel}",
            f"- Company brief: {'set' if store.get_setting('brief') else 'not set — use /brief'}",
        ]
        if s["channels"]:
            lines.append("- Busiest: " + ", ".join(f"#{n} ({c})" for n, c in s["channels"][:5]))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="forget", description="Delete everything logged from one member (CEO only)")
    @app_commands.describe(member="Whose messages to delete")
    @app_commands.default_permissions(manage_guild=True)
    async def forget(self, interaction: discord.Interaction, member: discord.Member):
        if await self._deny_unless_owner(interaction):
            return
        removed = store.forget_author(member.id)
        self.sessions.clear()
        await interaction.response.send_message(
            f"🗑️ Deleted {removed} logged messages from {member.display_name}.", ephemeral=True)

    # ------------------------------------------------------------ schedule

    @tasks.loop(minutes=5)
    async def scheduler(self):
        # An exception escaping a task loop stops it for good — never let one
        try:
            await self.run_due_jobs(datetime.now())
        except Exception as e:
            print(f"⚠️ Context Bot scheduler error: {e}")

    @scheduler.before_loop
    async def _before_scheduler(self):
        await self.bot.wait_until_ready()

    async def run_due_jobs(self, now: datetime):
        if self.guild() is None:
            return
        last_runs = {job: store.get_setting(f"last_{job}") for job in ("prune", "daily", "weekly")}
        for job in due_jobs(now, last_runs):
            # Marked first, so a job that fails doesn't retry every 5 minutes
            store.set_setting(f"last_{job}", now.date().isoformat())
            try:
                await getattr(self, f"_job_{job}")()
            except BrainError as e:
                # Otherwise a missing API key would fail silently every day
                label = {"daily": "morning brief", "weekly": "weekly report"}.get(job, job)
                await self.send_to_ceo(f"⚠️ Your {label} didn't go out: {e}")
            except Exception as e:
                print(f"⚠️ Context Bot {job} job failed: {e}")

    async def _job_prune(self):
        removed = store.prune(config.RETENTION_DAYS)
        if removed:
            print(f"🧹 Pruned {removed} messages older than {config.RETENTION_DAYS} days")

    async def _job_daily(self):
        now = datetime.now().astimezone()
        context, count, dropped = self.context(now - timedelta(days=1), now)
        if count == 0:
            return  # a quiet day needs no brief
        answer = await self.brain.run(self.system(), context, daily_task())
        store.save_report("daily", answer.text, days=1)
        await self.send_to_ceo(f"☀️ **Morning brief**\n{answer.text}\n{footer(1, count, dropped)}")

    async def _job_weekly(self):
        now = datetime.now().astimezone()
        context, count, dropped = self.context(now - timedelta(days=7), now)
        if count == 0:
            await self.send_to_ceo("📊 **Weekly report**\nNo team activity logged this week.")
            return
        system = self.system()
        # Same transcript for both, so the second reads it from the cache
        report = await self.brain.run(system, context, report_task(7))
        store.save_report("report", report.text, days=7)
        scorecard = await self.brain.run(system, context,
                                         scorecard_task(7, store.last_report("scorecard")))
        store.save_report("scorecard", scorecard.text, days=7)
        await self.send_to_ceo(f"📊 **Weekly report**\n{report.text}\n{footer(7, count, dropped)}")
        await self.send_to_ceo(f"⚖️ **Team scorecard**\n{scorecard.text}")

    async def send_to_ceo(self, text: str):
        for owner_id in self.owner_ids():
            try:
                user = self.bot.get_user(owner_id) or await self.bot.fetch_user(owner_id)
                await self._deliver(user.send, text)
            except discord.HTTPException as e:
                print(f"⚠️ Couldn't DM the CEO ({owner_id}): {e}")
        if config.REPORT_CHANNEL_ID:
            channel = self.bot.get_channel(config.REPORT_CHANNEL_ID)
            if channel is None:
                print(f"⚠️ Report channel {config.REPORT_CHANNEL_ID} not found")
                return
            await self._deliver(channel.send, text)
