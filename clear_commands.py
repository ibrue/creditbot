#!/usr/bin/env python3
"""One-time script to clear all Discord commands and re-register fresh."""

import asyncio
import discord
from discord.ext import commands
import config

async def clear_and_sync():
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user}")

        # Clear ALL global commands
        print("Clearing all global commands...")
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        print("All commands cleared!")

        # Close the bot
        await bot.close()

    await bot.start(config.DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(clear_and_sync())
