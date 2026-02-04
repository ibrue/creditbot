import asyncio
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
        print(f"{'='*50}\n")

        # Set bot status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="your social credit"
            )
        )


def main():
    # Validate configuration
    if config.DISCORD_TOKEN == "your-bot-token-here":
        print("❌ ERROR: Please set your Discord bot token!")
        print("   Edit config.py or set DISCORD_TOKEN environment variable")
        return

    if config.CHECKIN_CHANNEL_ID == 0:
        print("⚠️  WARNING: CHECKIN_CHANNEL_ID not set")
        print("   The bot will not post daily check-in messages")

    # Create and run bot
    bot = SocialCreditBot()

    try:
        bot.run(config.DISCORD_TOKEN)
    except discord.LoginFailure:
        print("❌ ERROR: Invalid Discord token!")
    except Exception as e:
        print(f"❌ ERROR: {e}")


if __name__ == "__main__":
    main()
