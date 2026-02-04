#!/usr/bin/env python3
"""Clear ALL commands (global and guild-specific) from Discord."""
import asyncio
import discord
from discord.ext import commands
import config

async def clear_all():
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user}")

        # Clear global commands
        print("Clearing global commands...")
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        print("Global commands cleared!")

        # Clear commands for each guild the bot is in
        for guild in bot.guilds:
            print(f"Clearing commands for guild: {guild.name} (ID: {guild.id})")
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"  Cleared commands for {guild.name}")

        print("\nAll commands cleared! Waiting 5 seconds before closing...")
        await asyncio.sleep(5)
        await bot.close()

    await bot.start(config.DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(clear_all())
