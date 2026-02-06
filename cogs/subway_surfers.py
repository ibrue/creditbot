import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, time, date
import queue

import config
import database
from utils.helpers import format_duration


class SubwaySurfersCog(commands.Cog):
    """Controls the Subway Surfers Pi kiosk and handles daily lab log exports."""

    VALID_MODES = {
        "subway": "subway_surfers",
        "minecraft": "minecraft_parkour",
        "ragebait": "ragebait",
        "stop": "stop",
    }

    MODE_DISPLAY = {
        "subway_surfers": "Subway Surfers",
        "minecraft_parkour": "Minecraft Parkour",
        "ragebait": "Ragebait",
    }

    def __init__(self, bot: commands.Bot, command_queue: queue.Queue, kiosk_app):
        self.bot = bot
        self.command_queue = command_queue
        self.kiosk_app = kiosk_app
        self._processed_interactions = set()
        self._max_tracked_interactions = 1000

        if config.LAB_LOG_CHANNEL_ID != 0:
            self.daily_lab_export.start()

    def _check_duplicate(self, interaction_id: int) -> bool:
        if interaction_id in self._processed_interactions:
            return True
        if len(self._processed_interactions) >= self._max_tracked_interactions:
            to_remove = list(self._processed_interactions)[:self._max_tracked_interactions // 2]
            for item in to_remove:
                self._processed_interactions.discard(item)
        self._processed_interactions.add(interaction_id)
        return False

    def cog_unload(self):
        self.daily_lab_export.cancel()

    @app_commands.command(name="play", description="Control the Subway Surfers Pi kiosk")
    @app_commands.describe(mode="What to play on the kiosk")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Subway Surfers", value="subway"),
        app_commands.Choice(name="Minecraft Parkour", value="minecraft"),
        app_commands.Choice(name="Ragebait", value="ragebait"),
        app_commands.Choice(name="Stop", value="stop"),
        app_commands.Choice(name="Status", value="status"),
    ])
    async def play(self, interaction: discord.Interaction, mode: str):
        if self._check_duplicate(interaction.id):
            return

        # Status works even without kiosk
        if mode == "status":
            await self._send_status(interaction)
            return

        if self.kiosk_app is None:
            await interaction.response.send_message(
                "Kiosk is not enabled on this instance.", ephemeral=True
            )
            return

        mode_key = self.VALID_MODES.get(mode)
        if not mode_key:
            await interaction.response.send_message("Unknown mode.", ephemeral=True)
            return

        self.command_queue.put(f"mode:{mode_key}")

        if mode_key == "stop":
            await interaction.response.send_message("Playback stopped. Kiosk is idle.")
        else:
            display_name = self.MODE_DISPLAY.get(mode_key, mode_key)
            await interaction.response.send_message(f"Now playing: **{display_name}**")

    async def _send_status(self, interaction: discord.Interaction):
        if self.kiosk_app is None:
            embed = discord.Embed(
                title="Subway Surfers Pi - Status",
                description="Kiosk is not enabled on this instance.",
                color=discord.Color.greyple(),
            )
            await interaction.response.send_message(embed=embed)
            return

        status = self.kiosk_app.get_status()
        uptime = status["uptime_seconds"]
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)

        color = discord.Color.green() if status["mode_key"] else discord.Color.greyple()
        embed = discord.Embed(
            title="Subway Surfers Pi - Status",
            color=color,
        )
        embed.add_field(name="Mode", value=status["mode"], inline=True)
        embed.add_field(
            name="Uptime",
            value=f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            inline=True,
        )
        embed.add_field(
            name="Bot",
            value="Connected" if status["bot_connected"] else "Disconnected",
            inline=True,
        )
        await interaction.response.send_message(embed=embed)

    # --- Daily lab log export ---

    @tasks.loop(time=time(hour=23, minute=59))
    async def daily_lab_export(self):
        """Export today's lab log to the backup Discord channel."""
        channel = self.bot.get_channel(config.LAB_LOG_CHANNEL_ID)
        if not channel:
            print(f"Could not find lab log channel {config.LAB_LOG_CHANNEL_ID}")
            return

        today = date.today()
        log = database.get_daily_lab_log(today)

        if not log:
            embed = discord.Embed(
                title=f"Lab Log - {today.strftime('%A, %B %d %Y')}",
                description="No lab activity today.",
                color=discord.Color.greyple(),
            )
            await channel.send(embed=embed)
            return

        total_minutes = 0
        total_credits = 0
        entries = []

        for session in log:
            username = session["username"]
            checkin = datetime.fromisoformat(session["checkin_time"])
            checkout_str = "Still in"
            duration_str = "ongoing"
            credits = session["credits_earned"] or 0

            if session["checkout_time"]:
                checkout = datetime.fromisoformat(session["checkout_time"])
                duration_min = int((checkout - checkin).total_seconds() / 60)
                total_minutes += duration_min
                duration_str = format_duration(duration_min)
                checkout_str = checkout.strftime("%I:%M %p")

            total_credits += credits
            entries.append(
                f"**{username}** - {checkin.strftime('%I:%M %p')} to {checkout_str} "
                f"({duration_str}, +{credits} credits)"
            )

        total_hours = total_minutes // 60
        total_mins = total_minutes % 60

        embed = discord.Embed(
            title=f"Lab Log - {today.strftime('%A, %B %d %Y')}",
            description="\n".join(entries),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Summary",
            value=(
                f"**{len(log)}** sessions | "
                f"**{total_hours}h {total_mins}m** total lab time | "
                f"**+{total_credits}** credits earned"
            ),
            inline=False,
        )
        embed.set_footer(text="Daily lab log backup")
        await channel.send(embed=embed)
        print(f"Lab log exported for {today}")

    @daily_lab_export.before_loop
    async def before_daily_export(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    kiosk_queue = getattr(bot, "kiosk_queue", queue.Queue())
    kiosk_app = getattr(bot, "kiosk_app", None)
    await bot.add_cog(SubwaySurfersCog(bot, kiosk_queue, kiosk_app))
