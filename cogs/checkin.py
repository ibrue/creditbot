import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, time, timedelta
import config
import database
from utils.helpers import format_duration, is_night_owl_time, is_weekend


class CheckinCog(commands.Cog):
    """Handles reaction-based lab check-ins."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_checkin_post.start()
        self.auto_checkout.start()
        self._posting_checkin = False  # Lock to prevent duplicate posts
        self._seeding_credits = False  # Lock to prevent duplicate seed operations
        # Track processed interactions to prevent duplicates
        self._processed_interactions = set()
        self._max_tracked_interactions = 1000

    def _check_duplicate(self, interaction_id: int) -> bool:
        """Check if this interaction was already processed. Returns True if duplicate."""
        if interaction_id in self._processed_interactions:
            return True
        if len(self._processed_interactions) >= self._max_tracked_interactions:
            to_remove = list(self._processed_interactions)[:self._max_tracked_interactions // 2]
            for item in to_remove:
                self._processed_interactions.discard(item)
        self._processed_interactions.add(interaction_id)
        return False

    def cog_unload(self):
        self.daily_checkin_post.cancel()
        self.auto_checkout.cancel()

    @tasks.loop(time=time(hour=config.DAILY_CHECKIN_HOUR, minute=0))
    async def daily_checkin_post(self):
        """Post the daily check-in message."""
        # An unhandled exception would permanently stop this loop until
        # the next bot restart — never let one escape
        try:
            await self._daily_checkin_post()
        except Exception as e:
            print(f"⚠️ Daily check-in post failed (will retry tomorrow): {e}")

    async def _daily_checkin_post(self):
        if config.CHECKIN_CHANNEL_ID == 0:
            return

        channel = self.bot.get_channel(config.CHECKIN_CHANNEL_ID)
        if not channel:
            print(f"⚠️ Could not find check-in channel {config.CHECKIN_CHANNEL_ID}")
            return

        today = datetime.now().strftime("%A, %B %d")
        weekend_bonus = "🎉 **WEEKEND WARRIOR BONUS ACTIVE** (+5 credits)\n" if is_weekend() else ""

        embed = discord.Embed(
            title="🔬 Lab Check-In",
            description=(
                f"**{today}**\n\n"
                f"{weekend_bonus}"
                f"**Option 1:** React with {config.CHECKIN_EMOJI} / {config.CHECKOUT_EMOJI}\n"
                f"**Option 2:** Use `/checkin` and `/checkout` commands\n\n"
                f"*Earn {config.CREDITS['lab_time_per_30_min']} credit per 30 minutes in the lab!*\n"
                f"*Use slash commands for multiple sessions per day!*"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="First to check in gets +3 bonus credits!")

        message = await channel.send(embed=embed)
        await message.add_reaction(config.CHECKIN_EMOJI)
        await message.add_reaction(config.CHECKOUT_EMOJI)

        # Save message ID to database
        database.save_daily_checkin_message(str(message.id), str(channel.id))
        print(f"✅ Posted daily check-in message: {message.id}")

    @daily_checkin_post.before_loop
    async def before_daily_checkin(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def auto_checkout(self):
        """Auto-checkout users who forgot to check out."""
        try:
            await self._auto_checkout()
            # Piggyback hourly maintenance: prune stale tracking rows so
            # the database doesn't grow unbounded over a season
            database.prune_old_data()
        except Exception as e:
            print(f"⚠️ Auto-checkout sweep failed (will retry next hour): {e}")

    async def _auto_checkout(self):
        stale = database.get_stale_checkins(config.AUTO_CHECKOUT_HOURS)

        for checkin in stale:
            # No points earned for forgetting to check out (not a penalty, just no reward)
            database.force_checkout_no_points(checkin['id'])

            # Try to DM user
            try:
                user = await self.bot.fetch_user(int(checkin['discord_id']))
                await user.send(
                    f"⚠️ You forgot to check out! "
                    f"Auto-checked out after {config.AUTO_CHECKOUT_HOURS} hours.\n"
                    f"**No credits earned** for this session. Remember to check out next time!"
                )
            except:
                pass

            print(f"⏰ Auto-checkout (no points): {checkin['username']} ({checkin['discord_id']})")

    @auto_checkout.before_loop
    async def before_auto_checkout(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle check-in/out reactions."""
        # Ignore bot reactions
        if payload.user_id == self.bot.user.id:
            return

        # Check if this is a check-in message
        if not database.is_checkin_message(str(payload.message_id)):
            return

        emoji = str(payload.emoji)
        user = await self.bot.fetch_user(payload.user_id)

        if emoji == config.CHECKIN_EMOJI:
            await self._handle_checkin(user, payload.message_id)
        elif emoji == config.CHECKOUT_EMOJI:
            await self._handle_checkout(user)

    async def _handle_checkin(self, user: discord.User, message_id: int):
        """Process a check-in."""
        # Check if already checked in
        existing = database.get_active_checkin(str(user.id))
        if existing:
            try:
                await user.send("ℹ️ You're already checked in!")
            except:
                pass
            return

        # Check if first arrival
        is_first = database.is_first_checkin_today()

        # Start check-in
        database.start_checkin(str(user.id), user.name, str(message_id))

        # Update streak
        new_streak = database.update_streak(str(user.id), user.name)

        # Calculate bonuses
        bonuses = []

        if is_first:
            database.add_credits(
                str(user.id), user.name,
                config.CREDITS['first_arrival'],
                "First arrival bonus"
            )
            bonuses.append(f"+{config.CREDITS['first_arrival']} First arrival!")

        if is_night_owl_time(config.NIGHT_OWL_HOUR):
            database.add_credits(
                str(user.id), user.name,
                config.CREDITS['night_owl'],
                "Night owl bonus"
            )
            bonuses.append(f"+{config.CREDITS['night_owl']} Night owl!")

        if new_streak > 1 and new_streak <= 7:
            # Only give streak bonus once per day
            if database.can_earn_streak_bonus(str(user.id)):
                database.add_credits(
                    str(user.id), user.name,
                    config.CREDITS['streak_bonus'],
                    f"Streak bonus (day {new_streak})"
                )
                database.record_streak_bonus(str(user.id))
                bonuses.append(f"+{config.CREDITS['streak_bonus']} Streak day {new_streak}!")
            else:
                bonuses.append(f"🔥 Streak day {new_streak}! (bonus already claimed today)")
        elif new_streak > 7:
            bonuses.append(f"🔥 Streak day {new_streak}! (bonus capped at 7 days)")

        # Send confirmation
        bonus_text = "\n".join(bonuses) if bonuses else ""
        try:
            await user.send(
                f"✅ **Checked in!**\n"
                f"Time: {datetime.now().strftime('%I:%M %p')}\n"
                f"{bonus_text}"
            )
        except:
            pass

        print(f"✅ Check-in: {user.name}")

    async def _handle_checkout(self, user: discord.User):
        """Process a check-out."""
        result = database.end_checkin(str(user.id))

        if not result:
            try:
                await user.send("ℹ️ You're not checked in!")
            except:
                pass
            return

        duration_str = format_duration(result['duration_minutes'])
        weekend_note = " (includes weekend bonus!)" if result.get('weekend_bonus') else ""

        try:
            await user.send(
                f"👋 **Checked out!**\n"
                f"Duration: {duration_str}\n"
                f"Credits earned: **+{result['credits_earned']}**{weekend_note}"
            )
        except:
            pass

        print(f"👋 Check-out: {user.name} ({duration_str}, +{result['credits_earned']} credits)")

    @app_commands.command(name="checkin", description="Check in to the lab")
    async def checkin_command(self, interaction: discord.Interaction):
        """Slash command to check in."""
        if self._check_duplicate(interaction.id):
            return

        user = interaction.user

        # Check if already checked in
        existing = database.get_active_checkin(str(user.id))
        if existing:
            checkin_time = datetime.fromisoformat(existing['checkin_time'])
            duration = int((datetime.now() - checkin_time).total_seconds() / 60)
            await interaction.response.send_message(
                f"ℹ️ You're already checked in! (since {checkin_time.strftime('%I:%M %p')}, {duration} min ago)\n"
                f"Use `/checkout` when you leave.",
                ephemeral=True
            )
            return

        # Check if first arrival
        is_first = database.is_first_checkin_today()

        # Start check-in
        database.start_checkin(str(user.id), user.name, "slash_command")

        # Update streak
        new_streak = database.update_streak(str(user.id), user.name)

        # Calculate bonuses
        bonuses = []

        if is_first:
            database.add_credits(
                str(user.id), user.name,
                config.CREDITS['first_arrival'],
                "First arrival bonus"
            )
            bonuses.append(f"+{config.CREDITS['first_arrival']} First arrival!")

        if is_night_owl_time(config.NIGHT_OWL_HOUR):
            database.add_credits(
                str(user.id), user.name,
                config.CREDITS['night_owl'],
                "Night owl bonus"
            )
            bonuses.append(f"+{config.CREDITS['night_owl']} Night owl!")

        if new_streak > 1 and new_streak <= 7:
            # Only give streak bonus once per day
            if database.can_earn_streak_bonus(str(user.id)):
                database.add_credits(
                    str(user.id), user.name,
                    config.CREDITS['streak_bonus'],
                    f"Streak bonus (day {new_streak})"
                )
                database.record_streak_bonus(str(user.id))
                bonuses.append(f"+{config.CREDITS['streak_bonus']} Streak day {new_streak}!")
            else:
                bonuses.append(f"🔥 Streak day {new_streak}! (bonus already claimed today)")
        elif new_streak > 7:
            bonuses.append(f"🔥 Streak day {new_streak}! (bonus capped at 7 days)")

        bonus_text = "\n".join(bonuses) if bonuses else ""

        await interaction.response.send_message(
            f"✅ **{user.mention} checked in!**\n"
            f"Time: {datetime.now().strftime('%I:%M %p')}\n"
            f"{bonus_text}\n"
            f"*Use `/checkout` when you leave!*"
        )

        print(f"✅ Check-in (slash): {user.name}")

    @app_commands.command(name="checkout", description="Check out from the lab")
    async def checkout_command(self, interaction: discord.Interaction):
        """Slash command to check out."""
        if self._check_duplicate(interaction.id):
            return

        user = interaction.user
        result = database.end_checkin(str(user.id))

        if not result:
            await interaction.response.send_message(
                "ℹ️ You're not checked in! Use `/checkin` first.",
                ephemeral=True
            )
            return

        duration_str = format_duration(result['duration_minutes'])
        weekend_note = " (includes weekend bonus!)" if result.get('weekend_bonus') else ""

        await interaction.response.send_message(
            f"👋 **{user.mention} checked out!**\n"
            f"Duration: {duration_str}\n"
            f"Credits earned: **+{result['credits_earned']}**{weekend_note}"
        )

        print(f"👋 Check-out (slash): {user.name} ({duration_str}, +{result['credits_earned']} credits)")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handle reaction removal (optional: could auto-checkout on ✅ removal)."""
        pass  # We don't auto-checkout on reaction removal for now

    @app_commands.command(name="post-checkin", description="[Admin] Manually post today's check-in message")
    @app_commands.default_permissions(manage_guild=True)
    async def post_checkin(self, interaction: discord.Interaction):
        """Manually trigger the daily check-in post."""
        # Prevent duplicate processing from duplicate interactions
        if self._check_duplicate(interaction.id):
            return

        # Prevent duplicate posts from multiple command invocations
        if self._posting_checkin:
            await interaction.response.send_message(
                "⏳ Already posting a check-in message, please wait...",
                ephemeral=True
            )
            return

        self._posting_checkin = True
        try:
            channel = self.bot.get_channel(config.CHECKIN_CHANNEL_ID)
            if not channel:
                await interaction.response.send_message(
                    "❌ Check-in channel not configured!",
                    ephemeral=True
                )
                return

            today = datetime.now().strftime("%A, %B %d")
            weekend_bonus = "🎉 **WEEKEND WARRIOR BONUS ACTIVE** (+5 credits)\n" if is_weekend() else ""

            embed = discord.Embed(
                title="🔬 Lab Check-In",
                description=(
                    f"**{today}**\n\n"
                    f"{weekend_bonus}"
                    f"**Option 1:** React with {config.CHECKIN_EMOJI} / {config.CHECKOUT_EMOJI}\n"
                    f"**Option 2:** Use `/checkin` and `/checkout` commands\n\n"
                    f"*Earn {config.CREDITS['lab_time_per_30_min']} credit per 30 minutes in the lab!*\n"
                    f"*Use slash commands for multiple sessions per day!*"
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text="First to check in gets +3 bonus credits!")

            message = await channel.send(embed=embed)
            await message.add_reaction(config.CHECKIN_EMOJI)
            await message.add_reaction(config.CHECKOUT_EMOJI)

            database.save_daily_checkin_message(str(message.id), str(channel.id))

            await interaction.response.send_message(
                f"✅ Check-in message posted in {channel.mention}!",
                ephemeral=True
            )
        finally:
            self._posting_checkin = False

    @app_commands.command(name="seed-credits", description="[Admin] Give baseline credits based on recent server activity")
    @app_commands.default_permissions(manage_guild=True)
    async def seed_credits(self, interaction: discord.Interaction):
        """Scan recent messages and give baseline credits to active members."""
        # Prevent duplicate processing from duplicate interactions
        if self._check_duplicate(interaction.id):
            return

        # Prevent duplicate processing
        if self._seeding_credits:
            await interaction.response.send_message(
                "⏳ Already seeding credits, please wait...",
                ephemeral=True
            )
            return

        self._seeding_credits = True
        try:
            await interaction.response.defer(ephemeral=True)

            # Get all text channels
            guild = interaction.guild
            two_weeks_ago = datetime.now() - timedelta(days=14)

            member_messages = {}  # {user_id: message_count}
            channels_scanned = 0

            status_msg = await interaction.followup.send(
                "🔍 Scanning server activity from the last 2 weeks...",
                ephemeral=True
            )

            for channel in guild.text_channels:
                try:
                    async for message in channel.history(after=two_weeks_ago, limit=5000):
                        if message.author.bot:
                            continue

                        user_id = str(message.author.id)
                        if user_id not in member_messages:
                            member_messages[user_id] = {
                                'count': 0,
                                'name': message.author.name
                            }
                        member_messages[user_id]['count'] += 1

                    channels_scanned += 1
                except discord.Forbidden:
                    continue  # Skip channels we can't read
                except Exception as e:
                    print(f"Error scanning {channel.name}: {e}")
                    continue

            # Award equal credits to anyone who has been active
            # Everyone gets 15 credits if they've sent at least 1 message
            baseline_credits = 15
            credits_awarded = []

            for user_id, data in member_messages.items():
                msg_count = data['count']
                username = data['name']

                if msg_count >= 1:
                    database.add_credits(
                        user_id, username,
                        baseline_credits,
                        f"Baseline: active in last 2 weeks"
                    )
                    credits_awarded.append((username, baseline_credits, msg_count))

            # Sort by credits awarded
            credits_awarded.sort(key=lambda x: x[1], reverse=True)

            # Build response
            if credits_awarded:
                top_10 = credits_awarded[:10]
                response = f"✅ **Baseline Credits Awarded!**\n\n"
                response += f"Scanned {channels_scanned} channels, {len(member_messages)} active members\n\n"
                response += "**Top Recipients:**\n"
                for username, credits, msgs in top_10:
                    response += f"• **{username}**: +{credits} credits ({msgs} messages)\n"

                if len(credits_awarded) > 10:
                    response += f"\n...and {len(credits_awarded) - 10} more members!"
            else:
                response = "No activity found in the last 2 weeks."

            await status_msg.edit(content=response)
        finally:
            self._seeding_credits = False

    @app_commands.command(name="force-checkout", description="[Admin] Force checkout a user")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        user="The user to check out",
        award_points="Whether to award points for time spent (default: True)"
    )
    async def force_checkout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        award_points: bool = True
    ):
        """Admin command to force checkout another user."""
        if self._check_duplicate(interaction.id):
            return

        # Check if user is checked in
        active_checkin = database.get_active_checkin(str(user.id))
        if not active_checkin:
            await interaction.response.send_message(
                f"ℹ️ {user.mention} is not currently checked in!",
                ephemeral=True
            )
            return

        if award_points:
            # Normal checkout with points
            result = database.end_checkin(str(user.id))
            duration_str = format_duration(result['duration_minutes'])
            weekend_note = " (includes weekend bonus!)" if result.get('weekend_bonus') else ""

            embed = discord.Embed(
                title="⚙️ Admin Force Checkout",
                description=(
                    f"{interaction.user.mention} checked out {user.mention}\n\n"
                    f"**Duration:** {duration_str}\n"
                    f"**Credits earned:** +{result['credits_earned']}{weekend_note}"
                ),
                color=discord.Color.orange()
            )
        else:
            # Checkout without points
            database.force_checkout_no_points(active_checkin['id'])
            checkin_time = datetime.fromisoformat(active_checkin['checkin_time'])
            duration_minutes = int((datetime.now() - checkin_time).total_seconds() / 60)
            duration_str = format_duration(duration_minutes)

            embed = discord.Embed(
                title="⚙️ Admin Force Checkout",
                description=(
                    f"{interaction.user.mention} checked out {user.mention}\n\n"
                    f"**Duration:** {duration_str}\n"
                    f"**Credits earned:** 0 (no points awarded)"
                ),
                color=discord.Color.orange()
            )

        await interaction.response.send_message(embed=embed)

        # Try to notify the user
        try:
            await user.send(
                f"⚠️ You were checked out by an admin.\n"
                f"{'Points were awarded for your time.' if award_points else 'No points were awarded.'}"
            )
        except:
            pass

        print(f"⚙️ Admin force-checkout: {user.name} by {interaction.user.name}")

    @app_commands.command(name="whos-in", description="See who is currently checked in to the lab")
    async def whos_in(self, interaction: discord.Interaction):
        """Show all users currently checked in."""
        if self._check_duplicate(interaction.id):
            return

        checked_in = database.get_all_checked_in()

        if not checked_in:
            await interaction.response.send_message(
                "🔬 **Nobody is currently checked in!**\n"
                "Use `/checkin` or react to the check-in message to check in."
            )
            return

        embed = discord.Embed(
            title="🔬 Currently in the Lab",
            description=f"**{len(checked_in)}** people checked in",
            color=discord.Color.green()
        )

        lab_list = ""
        for person in checked_in:
            checkin_time = datetime.fromisoformat(person['checkin_time'])
            duration_minutes = int((datetime.now() - checkin_time).total_seconds() / 60)
            hours = duration_minutes // 60
            mins = duration_minutes % 60

            if hours > 0:
                time_str = f"{hours}h {mins}m"
            else:
                time_str = f"{mins}m"

            lab_list += f"• <@{person['discord_id']}> - {time_str}\n"

        embed.add_field(
            name="Checked In",
            value=lab_list,
            inline=False
        )

        embed.set_footer(text=f"Use /checkout when you leave!")

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(CheckinCog(bot))
