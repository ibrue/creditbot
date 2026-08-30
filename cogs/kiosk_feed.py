"""Posts kiosk check-in photos to the check-in channel.

The kiosk API queues photos in the kiosk_photos table; this cog polls
the queue, asks the local LLM (Ollama, optional) for a fun caption, and
posts the photo + caption to the check-in channel.
"""
import asyncio
import json
import os

import discord
from discord.ext import commands, tasks

import caption as caption_mod
import config
import database


class KioskFeedCog(commands.Cog):
    """Posts kiosk check-in photos (with optional AI captions) to Discord."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.post_kiosk_photos.start()

    def cog_unload(self):
        self.post_kiosk_photos.cancel()

    @tasks.loop(seconds=10)
    async def post_kiosk_photos(self):
        try:
            await self._post_kiosk_photos()
        except Exception as e:
            print(f"⚠️ Kiosk photo posting failed (will retry): {e}")

    async def _post_kiosk_photos(self):
        if not config.KIOSK_POST_PHOTOS or config.CHECKIN_CHANNEL_ID == 0:
            return

        pending = database.get_unposted_kiosk_photos()
        if not pending:
            return

        channel = self.bot.get_channel(config.CHECKIN_CHANNEL_ID)
        if not channel:
            return

        for photo in pending:
            path = photo["photo_path"]
            if not path or not os.path.exists(path):
                # File vanished — drop the queue entry
                database.mark_kiosk_photo_posted(photo["id"])
                continue

            # Caption via local LLM (runs in a thread; can take a while on CPU)
            photo_caption = None
            try:
                photo_caption = await asyncio.to_thread(
                    caption_mod.generate_caption, path
                )
            except Exception as e:
                print(f"⚠️ Caption generation failed: {e}")

            try:
                bonuses = json.loads(photo["bonuses"]) if photo["bonuses"] else []
            except (json.JSONDecodeError, TypeError):
                bonuses = []

            # Departures are photographed too, so say which one happened.
            leaving = (photo["action"] if "action" in photo.keys() else "in") == "out"
            verb = "just checked out of" if leaving else "just checked in at"
            description = f"<@{photo['discord_id']}> {verb} the kiosk!"
            if photo_caption:
                description += f"\n\n🤖 *“{photo_caption}”*"
            if bonuses:
                description += "\n\n" + "\n".join(bonuses)

            embed = discord.Embed(
                title="👋 Kiosk Check-Out" if leaving else "📸 Kiosk Check-In",
                description=description,
                color=discord.Color.orange() if leaving else discord.Color.green(),
            )
            filename = "checkout.jpg" if leaving else "checkin.jpg"
            embed.set_image(url=f"attachment://{filename}")

            try:
                await channel.send(
                    embed=embed,
                    file=discord.File(path, filename=filename),
                )
            except Exception as e:
                print(f"⚠️ Could not post kiosk photo (will retry): {e}")
                return  # retry this photo on the next loop

            database.mark_kiosk_photo_posted(photo["id"])
            try:
                os.remove(path)
            except OSError:
                pass
            print(f"📸 Posted kiosk check-in photo for {photo['username']}")

    @post_kiosk_photos.before_loop
    async def before_post_kiosk_photos(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(KioskFeedCog(bot))
