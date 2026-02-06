import asyncio
import threading
import queue as queue_module
import discord
from discord.ext import commands
import config
import database


class SocialCreditBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        self.synced = False  # Track if we've synced commands
        self.kiosk_queue = queue_module.Queue()
        self.kiosk_app = None

    async def setup_hook(self):
        """Load all cogs and sync commands."""
        # Initialize database
        database.init_database()
        print("Database initialized")

        # Load cogs
        cogs = [
            "cogs.checkin",
            "cogs.social_credit",
            "cogs.leaderboard",
            "cogs.subway_surfers",
        ]

        for cog in cogs:
            try:
                await self.load_extension(cog)
                print(f"Loaded {cog}")
            except Exception as e:
                print(f"Failed to load {cog}: {e}")

    async def on_ready(self):
        # Only sync commands once per session
        if not self.synced:
            try:
                # Sync commands to Discord
                synced = await self.tree.sync()
                print(f"Synced {len(synced)} slash commands")
                self.synced = True
            except Exception as e:
                print(f"Failed to sync commands: {e}")

        print(f"\n{'='*50}")
        print(f"Bot is online!")
        print(f"Social Credit System Active")
        if config.KIOSK_ENABLED:
            print(f"Kiosk mode: ENABLED")
        print(f"{'='*50}\n")

        # Set bot status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="your social credit"
            )
        )

        # Signal kiosk that bot is connected
        self.kiosk_queue.put("bot_connected")


def main():
    # Validate configuration
    if config.DISCORD_TOKEN == "your-bot-token-here":
        print("ERROR: Please set your Discord bot token!")
        print("   Edit config.py or set DISCORD_TOKEN environment variable")
        return

    if config.CHECKIN_CHANNEL_ID == 0:
        print("WARNING: CHECKIN_CHANNEL_ID not set")
        print("   The bot will not post daily check-in messages")

    # Create bot
    bot = SocialCreditBot()

    if config.KIOSK_ENABLED:
        # Kiosk mode: tkinter on main thread, bot on daemon thread
        from kiosk.gui import KioskApp

        kiosk_queue = queue_module.Queue()
        kiosk_app = KioskApp(kiosk_queue, config.KIOSK_VIDEO_DIR, config.KIOSK_FULLSCREEN)

        bot.kiosk_queue = kiosk_queue
        bot.kiosk_app = kiosk_app

        def run_bot():
            bot.run(config.DISCORD_TOKEN)

        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()

        print("Kiosk GUI starting on main thread...")
        kiosk_app.run()
    else:
        # Normal mode: bot on main thread (existing behavior)
        try:
            bot.run(config.DISCORD_TOKEN)
        except discord.LoginFailure:
            print("ERROR: Invalid Discord token!")
        except Exception as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
