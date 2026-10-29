"""Start Context Bot:  python -m contextbot"""
import os
import time
import traceback

import discord
from discord.ext import commands

from . import config, store
from .cog import ContextCog


class ContextBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # to read what the team writes
        intents.members = True          # to see who is on the team, quiet or not
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         help_command=None)
        self.synced = False

    async def setup_hook(self):
        store.init()
        await self.add_cog(ContextCog(self))
        self.tree.on_error = self.on_app_command_error

    async def on_message(self, message):
        """No prefix commands — the cog does the listening."""

    async def on_error(self, event_method: str, *args, **kwargs):
        print(f"⚠️ Unhandled error in {event_method}:")
        traceback.print_exc()

    async def on_app_command_error(self, interaction: discord.Interaction, error):
        print(f"⚠️ Slash command error in /{getattr(interaction.command, 'name', '?')}: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)
        try:
            msg = "⚠️ Something went wrong running that command. Try again in a moment."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    async def on_ready(self):
        cog = self.get_cog("ContextCog")
        guild = cog.guild() if cog else None
        if guild is None:
            if config.GUILD_ID:
                print(f"⚠️ Not in the server CONTEXTBOT_GUILD_ID={config.GUILD_ID}. "
                      f"Invite the bot there (see contextbot/README.md).")
            elif self.guilds:
                print(f"⚠️ In {len(self.guilds)} servers — set CONTEXTBOT_GUILD_ID "
                      f"to the one to work in. Doing nothing until then.")
            else:
                print("⚠️ Not in any server yet. Invite the bot (see contextbot/README.md).")
            return

        # Commands are registered on the one server only: they show up there
        # immediately, and nowhere else
        if not self.synced:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"Synced {len(synced)} slash commands to {guild.name}")
                self.synced = True
            except Exception as e:
                print(f"Failed to sync commands: {e}")

        print(f"\n{'=' * 50}\nContext Bot is online in {guild.name}\n"
              f"CEO: {', '.join(cog.owner_names()) or 'unknown'} · model {config.MODEL}\n"
              f"{'=' * 50}\n")
        if not config.ANTHROPIC_API_KEY:
            print("⚠️ ANTHROPIC_API_KEY is not set — messages are logged, "
                  "but reports will fail until it is.")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name="the team ship"))


def main():
    if not config.DISCORD_TOKEN:
        print("ℹ️ Context Bot is not set up: CONTEXTBOT_DISCORD_TOKEN is empty. "
              "See contextbot/README.md.")
        if os.getenv("CONTEXTBOT_WAIT_FOR_TOKEN") == "1":
            # Under Docker: wait quietly rather than restart-loop. Set the
            # token in .env, then `docker compose up -d contextbot`.
            while True:
                time.sleep(3600)
        return

    bot = ContextBot()
    try:
        bot.run(config.DISCORD_TOKEN)
    except discord.LoginFailure:
        print("❌ Discord rejected CONTEXTBOT_DISCORD_TOKEN. Reset it in the "
              "Developer Portal and paste the new one into .env.")
    except discord.PrivilegedIntentsRequired:
        print("❌ Turn on MESSAGE CONTENT INTENT and SERVER MEMBERS INTENT for "
              "Context Bot in the Developer Portal (Bot page), then restart.")


if __name__ == "__main__":
    main()
