import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, time
import config
import database
from utils.helpers import (
    format_duration, format_credits, get_rank_emoji,
    get_credit_tier, get_streak_message
)


class LeaderboardCog(commands.Cog):
    """Handles leaderboard, stats, and weekly announcements."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.weekly_announcement.start()
        self.daily_leaderboard.start()
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
        self.weekly_announcement.cancel()
        self.daily_leaderboard.cancel()

    async def get_or_create_winner_role(self, guild: discord.Guild) -> discord.Role:
        """Get the winner role, creating it if it doesn't exist."""
        # Check if role ID is configured
        if config.WINNER_ROLE_ID != 0:
            role = guild.get_role(config.WINNER_ROLE_ID)
            if role:
                return role

        # Look for role by name
        role = discord.utils.get(guild.roles, name=config.WINNER_ROLE_NAME)
        if role:
            return role

        # Create the role
        role = await guild.create_role(
            name=config.WINNER_ROLE_NAME,
            color=discord.Color(config.WINNER_ROLE_COLOR),
            hoist=True,  # Display separately in member list
            mentionable=True,
            reason="Social Credit System - Winner Role"
        )
        print(f"✅ Created winner role: {role.name} (ID: {role.id})")
        return role

    async def update_winner_role(self, guild: discord.Guild, winner_id: str):
        """Remove role from previous winner and assign to new winner."""
        try:
            role = await self.get_or_create_winner_role(guild)

            # Remove role from all current holders
            for member in role.members:
                try:
                    await member.remove_roles(role, reason="New weekly winner")
                    print(f"👑 Removed {role.name} from {member.name}")
                except Exception as e:
                    print(f"⚠️ Could not remove role from {member.name}: {e}")

            # Add role to new winner
            winner = guild.get_member(int(winner_id))
            if winner:
                await winner.add_roles(role, reason="Weekly Social Credit Winner!")
                print(f"👑 Assigned {role.name} to {winner.name}")
                return winner
            else:
                print(f"⚠️ Could not find winner member: {winner_id}")
                return None

        except discord.Forbidden:
            print("❌ Bot lacks permission to manage roles!")
            return None
        except Exception as e:
            print(f"❌ Error updating winner role: {e}")
            return None

    @tasks.loop(time=time(hour=config.WEEKLY_ANNOUNCEMENT_HOUR, minute=0))
    async def weekly_announcement(self):
        """Post weekly leaderboard and reset scores."""
        # Only run on the configured day
        today = datetime.now().strftime("%A").lower()
        if today != config.WEEKLY_ANNOUNCEMENT_DAY:
            return

        if config.ANNOUNCEMENTS_CHANNEL_ID == 0:
            # Fall back to checkin channel
            channel_id = config.CHECKIN_CHANNEL_ID
        else:
            channel_id = config.ANNOUNCEMENTS_CHANNEL_ID

        if channel_id == 0:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        # Get leaderboard
        leaderboard = database.get_weekly_leaderboard(10)

        if not leaderboard:
            await channel.send("📊 No social credit activity this week!")
            database.reset_weekly_credits()
            return

        # Build announcement embed
        embed = discord.Embed(
            title="🏆 WEEKLY SOCIAL CREDIT RESULTS",
            description="The Party has reviewed this week's contributions!",
            color=discord.Color.gold()
        )

        # Top 3 with special titles
        titles = [
            ("🏆 Supreme Leader", "Highest social credit!"),
            ("🥈 Comrade of the People", "Second highest!"),
            ("🥉 Rising Star", "Third place!"),
        ]

        for i, (title, subtitle) in enumerate(titles):
            if i < len(leaderboard):
                user = leaderboard[i]
                embed.add_field(
                    name=title,
                    value=(
                        f"<@{user['discord_id']}>\n"
                        f"**{user['weekly_credits']}** credits\n"
                        f"*{subtitle}*"
                    ),
                    inline=True
                )

        # Lab Rat award (most lab hours)
        lab_rat = database.get_most_lab_hours_this_week()
        if lab_rat:
            embed.add_field(
                name="⏰ Lab Rat",
                value=(
                    f"<@{lab_rat['discord_id']}>\n"
                    f"*Most time in the lab!*"
                ),
                inline=True
            )

        # Most Improved (compare to last week)
        most_improved = None
        best_improvement = 0

        for user in leaderboard:
            prev = database.get_previous_week_credits(user['discord_id'])
            improvement = user['weekly_credits'] - prev
            if improvement > best_improvement:
                best_improvement = improvement
                most_improved = user

        if most_improved and best_improvement > 0:
            embed.add_field(
                name="📈 Most Improved",
                value=(
                    f"<@{most_improved['discord_id']}>\n"
                    f"+{best_improvement} from last week!"
                ),
                inline=True
            )

        # Full leaderboard
        board_text = ""
        for i, user in enumerate(leaderboard[:10], 1):
            board_text += (
                f"{get_rank_emoji(i)} <@{user['discord_id']}> - "
                f"**{user['weekly_credits']}** credits\n"
            )

        embed.add_field(
            name="📊 Full Leaderboard",
            value=board_text or "No activity",
            inline=False
        )

        embed.set_footer(text="Weekly scores have been reset. May the odds be ever in your favor!")

        await channel.send(embed=embed)

        # Assign winner role to #1
        winner_id = None
        if leaderboard:
            winner_id = leaderboard[0]['discord_id']
            winner = await self.update_winner_role(channel.guild, winner_id)
            if winner:
                await channel.send(
                    f"{winner.mention} has been crowned **{config.WINNER_ROLE_NAME}** for the week!"
                )

                # Supreme Leader's power - magic smoke nomination
                await channel.send(
                    f"@everyone\n\n"
                    f"**SUPREME LEADER'S JUDGMENT**\n\n"
                    f"{winner.mention} now has the power to nominate ONE person for "
                    f"**Magic Smoke** punishment (-10 credits).\n\n"
                    f"Use `/supreme-smoke @user` to nominate someone. "
                    f"If 2+ others agree with `/agree-smoke`, the punishment will be applied.\n\n"
                    f"*This power expires in 24 hours.*\n\n"
                    f"**Note:** As Supreme Leader, {winner.mention} starts the new week at **-15 credits**. "
                    f"Heavy is the crown."
                )

        # Reset weekly credits
        database.reset_weekly_credits()

        # Apply Supreme Leader penalty - they start the new week at -15
        if winner_id:
            database.penalize_supreme_leader(winner_id, -15)

        print("Weekly announcement posted and scores reset")

    @weekly_announcement.before_loop
    async def before_weekly(self):
        await self.bot.wait_until_ready()

    @tasks.loop(time=time(hour=config.DAILY_LEADERBOARD_HOUR, minute=0))
    async def daily_leaderboard(self):
        """Post daily leaderboard at 7 PM."""
        if config.ANNOUNCEMENTS_CHANNEL_ID == 0:
            channel_id = config.CHECKIN_CHANNEL_ID
        else:
            channel_id = config.ANNOUNCEMENTS_CHANNEL_ID

        if channel_id == 0:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        # Get leaderboard
        leaders = database.get_weekly_leaderboard(10)

        if not leaders:
            return  # Don't post if no activity

        today = datetime.now().strftime("%A, %B %d")

        embed = discord.Embed(
            title="📊 Daily Leaderboard Update",
            description=f"**{today}** - Current weekly standings",
            color=discord.Color.blue()
        )

        board_text = ""
        for i, user in enumerate(leaders, 1):
            streak_indicator = f" 🔥{user['current_streak']}" if user['current_streak'] > 1 else ""
            board_text += (
                f"{get_rank_emoji(i)} <@{user['discord_id']}> - "
                f"**{user['weekly_credits']}** credits{streak_indicator}\n"
            )

        embed.add_field(
            name="🏆 Top 10 This Week",
            value=board_text,
            inline=False
        )

        # Show who's in the lead
        if leaders:
            leader = leaders[0]
            embed.set_footer(text=f"Current leader: {leader['username']} with {leader['weekly_credits']} credits")

        await channel.send(embed=embed)
        print("✅ Daily leaderboard posted")

    @daily_leaderboard.before_loop
    async def before_daily_leaderboard(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="post-leaderboard", description="[Admin] Manually post the daily leaderboard")
    @app_commands.default_permissions(manage_guild=True)
    async def post_leaderboard(self, interaction: discord.Interaction):
        """Manually trigger the daily leaderboard post."""
        if self._check_duplicate(interaction.id):
            return
        leaders = database.get_weekly_leaderboard(10)

        if not leaders:
            await interaction.response.send_message(
                "📊 No social credit activity this week yet!",
                ephemeral=True
            )
            return

        today = datetime.now().strftime("%A, %B %d")

        embed = discord.Embed(
            title="📊 Daily Leaderboard Update",
            description=f"**{today}** - Current weekly standings",
            color=discord.Color.blue()
        )

        board_text = ""
        for i, user in enumerate(leaders, 1):
            streak_indicator = f" 🔥{user['current_streak']}" if user['current_streak'] > 1 else ""
            board_text += (
                f"{get_rank_emoji(i)} <@{user['discord_id']}> - "
                f"**{user['weekly_credits']}** credits{streak_indicator}\n"
            )

        embed.add_field(
            name="🏆 Top 10 This Week",
            value=board_text,
            inline=False
        )

        if leaders:
            leader = leaders[0]
            embed.set_footer(text=f"Current leader: {leader['username']} with {leader['weekly_credits']} credits")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="post-rules", description="[Admin] Post the pinnable social credit rules message")
    @app_commands.default_permissions(manage_guild=True)
    async def post_rules(self, interaction: discord.Interaction):
        """Post a pinnable message explaining the social credit system."""
        if self._check_duplicate(interaction.id):
            return
        embed = discord.Embed(
            title="ROBOTICS SOCIAL CREDIT SYSTEM",
            description=(
                "Welcome to the Social Credit System. Earn credits by being active "
                "in the lab and helping your fellow team members. The member with "
                "the highest credits each week becomes the **Supreme Leader**."
            ),
            color=discord.Color.gold()
        )

        embed.add_field(
            name="How to Earn Credits",
            value=(
                "**Lab Time**\n"
                "- +1 credit per 30 minutes in the lab\n"
                "- +3 bonus for being first to check in\n"
                "- +2 bonus for night owl (after 8 PM)\n"
                "- +5 bonus for weekend lab time\n"
                "- +2 bonus per day streak\n\n"
                "**Activities**\n"
                "- +5 for helping others (`/thank @user`)\n"
                "- +3 for documentation (post images in notebooking)\n"
                "- +1 for memes (1x/day max)"
            ),
            inline=False
        )

        embed.add_field(
            name="How to Lose Credits",
            value=(
                "- -10 for releasing magic smoke (3+ votes)\n"
                "- -2 for forgetting to check out\n"
                "- -1 for getting roasted (5+ fire reactions)"
            ),
            inline=False
        )

        embed.add_field(
            name="Lab Check-In",
            value=(
                "React with checkmark to **check in** when you arrive\n"
                "React with X to **check out** when you leave\n"
                "A new check-in message posts daily at 5 PM"
            ),
            inline=False
        )

        embed.add_field(
            name="Commands",
            value=(
                "`/credit` - Check your score\n"
                "`/leaderboard` - Weekly standings\n"
                "`/stats` - Detailed statistics\n"
                "`/thank @user` - Thank someone\n"
                "`/history` - Recent transactions"
            ),
            inline=False
        )

        embed.add_field(
            name="Weekly Awards",
            value=(
                "Every Sunday at 6 PM:\n"
                "**Supreme Leader** - Highest credits (gets special role)\n"
                "**Comrade of the People** - Second place\n"
                "**Rising Star** - Third place\n"
                "**Lab Rat** - Most lab hours"
            ),
            inline=False
        )

        embed.set_footer(text="Daily leaderboard posts at 7 PM | Weekly reset on Sunday")

        await interaction.response.send_message(embed=embed)
        await interaction.followup.send("📌 **Tip:** Right-click the message above and select 'Pin Message' to keep it at the top!", ephemeral=True)

    @app_commands.command(name="credit", description="Check social credit score")
    @app_commands.describe(user="User to check (leave empty for yourself)")
    async def credit(
        self,
        interaction: discord.Interaction,
        user: discord.Member = None
    ):
        """Check social credit score."""
        if self._check_duplicate(interaction.id):
            return
        target = user or interaction.user
        user_data = database.get_user(str(target.id))

        if not user_data:
            await interaction.response.send_message(
                f"{target.mention} has no social credit record yet!",
                ephemeral=True
            )
            return

        tier_name, tier_emoji = get_credit_tier(user_data['total_credits'])

        embed = discord.Embed(
            title=f"{tier_emoji} {target.display_name}'s Social Credit",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="This Week",
            value=f"**{user_data['weekly_credits']}** credits",
            inline=True
        )

        embed.add_field(
            name="All Time",
            value=f"**{user_data['total_credits']}** credits",
            inline=True
        )

        embed.add_field(
            name="Rank",
            value=f"**{tier_name}**",
            inline=True
        )

        embed.set_thumbnail(url=target.display_avatar.url)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="stats", description="View detailed stats")
    @app_commands.describe(user="User to check (leave empty for yourself)")
    async def stats(
        self,
        interaction: discord.Interaction,
        user: discord.Member = None
    ):
        """View detailed statistics."""
        if self._check_duplicate(interaction.id):
            return
        target = user or interaction.user
        user_data = database.get_user(str(target.id))

        if not user_data:
            await interaction.response.send_message(
                f"{target.mention} has no stats yet!",
                ephemeral=True
            )
            return

        tier_name, tier_emoji = get_credit_tier(user_data['total_credits'])
        streak_msg = get_streak_message(user_data['current_streak'])

        embed = discord.Embed(
            title=f"{tier_emoji} {target.display_name}'s Stats",
            color=discord.Color.purple()
        )

        # Credits
        embed.add_field(
            name="💰 Credits",
            value=(
                f"This Week: **{user_data['weekly_credits']}**\n"
                f"All Time: **{user_data['total_credits']}**\n"
                f"Rank: **{tier_name}**"
            ),
            inline=True
        )

        # Lab Time
        total_hours = user_data['total_lab_minutes'] // 60
        total_mins = user_data['total_lab_minutes'] % 60
        embed.add_field(
            name="⏱️ Lab Time",
            value=f"**{total_hours}h {total_mins}m** total",
            inline=True
        )

        # Streaks
        embed.add_field(
            name="🔥 Streaks",
            value=(
                f"Current: **{user_data['current_streak']}** days\n"
                f"Longest: **{user_data['longest_streak']}** days\n"
                f"{streak_msg}"
            ),
            inline=True
        )

        embed.set_thumbnail(url=target.display_avatar.url)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Show the weekly leaderboard")
    async def leaderboard(self, interaction: discord.Interaction):
        """Show the weekly leaderboard."""
        if self._check_duplicate(interaction.id):
            return
        leaders = database.get_weekly_leaderboard(10)

        if not leaders:
            await interaction.response.send_message(
                "📊 No social credit activity this week yet!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="📊 Weekly Leaderboard",
            color=discord.Color.gold()
        )

        board_text = ""
        for i, user in enumerate(leaders, 1):
            streak_indicator = f" 🔥{user['current_streak']}" if user['current_streak'] > 1 else ""
            board_text += (
                f"{get_rank_emoji(i)} <@{user['discord_id']}> - "
                f"**{user['weekly_credits']}** credits{streak_indicator}\n"
            )

        embed.description = board_text
        embed.set_footer(text="Resets every Sunday at 6 PM")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="history", description="View your recent credit transactions")
    async def history(self, interaction: discord.Interaction):
        """View recent credit transactions."""
        if self._check_duplicate(interaction.id):
            return
        transactions = database.get_transactions(str(interaction.user.id), 10)

        if not transactions:
            await interaction.response.send_message(
                "📜 No transaction history yet!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="📜 Recent Transactions",
            color=discord.Color.blue()
        )

        history_text = ""
        for tx in transactions:
            amount = format_credits(tx['amount'])
            timestamp = datetime.fromisoformat(tx['timestamp']).strftime("%m/%d %I:%M%p")
            emoji = "✅" if tx['amount'] > 0 else "❌"
            history_text += f"{emoji} **{amount}** - {tx['reason']}\n*{timestamp}*\n\n"

        embed.description = history_text
        embed.set_footer(text=f"Showing last {len(transactions)} transactions")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="alltime", description="Show all-time leaderboard")
    async def alltime(self, interaction: discord.Interaction):
        """Show all-time leaderboard."""
        if self._check_duplicate(interaction.id):
            return
        leaders = database.get_all_time_leaderboard(10)

        if not leaders:
            await interaction.response.send_message(
                "📊 No all-time records yet!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🏅 All-Time Leaderboard",
            color=discord.Color.purple()
        )

        board_text = ""
        for i, user in enumerate(leaders, 1):
            hours = user['total_lab_minutes'] // 60
            board_text += (
                f"{get_rank_emoji(i)} <@{user['discord_id']}> - "
                f"**{user['total_credits']}** credits "
                f"({hours}h lab time)\n"
            )

        embed.description = board_text

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lab-log", description="View lab check-in history")
    @app_commands.describe(user="User to check (leave empty for yourself)")
    async def lab_log(
        self,
        interaction: discord.Interaction,
        user: discord.Member = None
    ):
        """View lab check-in/check-out history."""
        if self._check_duplicate(interaction.id):
            return
        target = user or interaction.user
        logs = database.get_checkin_history(str(target.id), 10)

        if not logs:
            await interaction.response.send_message(
                f"{target.mention} has no lab history yet!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🔬 {target.display_name}'s Lab Log",
            color=discord.Color.green()
        )

        log_text = ""
        for log in logs:
            checkin = datetime.fromisoformat(log['checkin_time'])
            date_str = checkin.strftime("%m/%d")
            time_in = checkin.strftime("%I:%M%p")

            if log['checkout_time']:
                checkout = datetime.fromisoformat(log['checkout_time'])
                time_out = checkout.strftime("%I:%M%p")
                duration = int((checkout - checkin).total_seconds() / 60)
                hours = duration // 60
                mins = duration % 60
                duration_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
                log_text += f"📅 **{date_str}**: {time_in} → {time_out} ({duration_str})\n"
            else:
                log_text += f"📅 **{date_str}**: {time_in} → *still checked in*\n"

        embed.description = log_text
        embed.set_footer(text=f"Showing last {len(logs)} sessions")

        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(name="ranks", description="View all social credit ranks and their requirements")
    async def ranks(self, interaction: discord.Interaction):
        """Show all available ranks."""
        if self._check_duplicate(interaction.id):
            return

        embed = discord.Embed(
            title="🏆 Social Credit Ranks",
            description="Earn credits to climb the ranks!",
            color=discord.Color.gold()
        )

        ranks_text = """
👑 **Supreme Leader** - 1000+ credits
⭐ **Comrade General** - 500+ credits
🎖️ **Party Member** - 250+ credits
🏅 **Loyal Citizen** - 100+ credits
📈 **Promising Worker** - 50+ credits
🌱 **New Recruit** - 10+ credits
❓ **Unranked** - 0-9 credits
💀 **Debt Collector** - Negative credits
"""
        embed.add_field(
            name="Rank Ladder",
            value=ranks_text,
            inline=False
        )

        embed.add_field(
            name="How to Earn Credits",
            value=(
                "• **Lab Time**: +1 per 30 min\n"
                "• **First Arrival**: +3 bonus\n"
                "• **Help Others**: +5 (`/thank @user`)\n"
                "• **Documentation**: +3\n"
                "• **Streaks**: +2 per day"
            ),
            inline=True
        )

        embed.add_field(
            name="Weekly Prize",
            value=(
                "The player with the most credits "
                "each week becomes the **Supreme Leader** "
                "and gets a special role!"
            ),
            inline=True
        )

        embed.set_footer(text="Use /credit to check your current rank!")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="set-credits", description="[Admin] Set a user's total credits to a specific value")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        user="The user to set credits for",
        amount="The new credit total (can be negative)",
        reason="Reason for setting credits"
    )
    async def set_credits(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: int,
        reason: str = "Admin set"
    ):
        """Set a user's credits to a specific value."""
        if self._check_duplicate(interaction.id):
            return

        # Can't set for bots
        if user.bot:
            await interaction.response.send_message(
                "🤖 Bots don't have social credit!",
                ephemeral=True
            )
            return

        # Get current credits
        user_data = database.get_user(str(user.id))
        current_total = user_data['total_credits'] if user_data else 0
        current_weekly = user_data['weekly_credits'] if user_data else 0

        # Calculate the difference needed
        diff = amount - current_total

        if diff == 0:
            await interaction.response.send_message(
                f"ℹ️ {user.mention} already has {amount} credits!",
                ephemeral=True
            )
            return

        # Add the difference (this creates a transaction)
        database.add_credits(
            str(user.id), user.name,
            diff,
            f"Admin set by {interaction.user.name}: {reason}"
        )

        embed = discord.Embed(
            title="⚙️ Credits Set",
            description=(
                f"{interaction.user.mention} set credits for {user.mention}\n\n"
                f"**Previous**: {current_total} credits\n"
                f"**New**: {amount} credits\n"
                f"**Change**: {diff:+d}\n"
                f"Reason: *{reason}*"
            ),
            color=discord.Color.orange()
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="audit", description="[Admin] Audit and fix credit discrepancies")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(fix="Set to True to automatically fix discrepancies")
    async def audit(self, interaction: discord.Interaction, fix: bool = False):
        """Audit all users' credits against transaction history."""
        if self._check_duplicate(interaction.id):
            return
        await interaction.response.defer(ephemeral=True)

        if fix:
            # Fix all credits
            fixed = database.fix_all_credits()
            if not fixed:
                await interaction.followup.send(
                    "✅ **Audit Complete**\nNo discrepancies found! All credits match transaction history.",
                    ephemeral=True
                )
                return

            response = f"🔧 **Fixed {len(fixed)} users' credits:**\n\n"
            for user in fixed[:10]:
                total_fix = f"{user['total_diff']:+d}" if user['total_diff'] != 0 else "OK"
                weekly_fix = f"{user['weekly_diff']:+d}" if user['weekly_diff'] != 0 else "OK"
                response += (
                    f"**{user['username']}**\n"
                    f"  Total: {user['stored_total']} → {user['calculated_total']} ({total_fix})\n"
                    f"  Weekly: {user['stored_weekly']} → {user['calculated_weekly']} ({weekly_fix})\n\n"
                )

            if len(fixed) > 10:
                response += f"...and {len(fixed) - 10} more users"

            await interaction.followup.send(response, ephemeral=True)
        else:
            # Just audit, don't fix
            discrepancies = database.audit_all_users()
            if not discrepancies:
                await interaction.followup.send(
                    "✅ **Audit Complete**\nNo discrepancies found! All credits match transaction history.",
                    ephemeral=True
                )
                return

            response = f"⚠️ **Found {len(discrepancies)} discrepancies:**\n\n"
            for user in discrepancies[:10]:
                total_diff = f"{user['total_diff']:+d}" if user['total_diff'] != 0 else "OK"
                weekly_diff = f"{user['weekly_diff']:+d}" if user['weekly_diff'] != 0 else "OK"
                response += (
                    f"**{user['username']}**\n"
                    f"  Total: stored={user['stored_total']}, calculated={user['calculated_total']} ({total_diff})\n"
                    f"  Weekly: stored={user['stored_weekly']}, calculated={user['calculated_weekly']} ({weekly_diff})\n\n"
                )

            if len(discrepancies) > 10:
                response += f"...and {len(discrepancies) - 10} more users\n\n"

            response += "Run `/audit fix:True` to fix all discrepancies."

            await interaction.followup.send(response, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LeaderboardCog(bot))
