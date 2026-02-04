import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
import config
import database
from utils.helpers import format_credits


class SocialCreditCog(commands.Cog):
    """Handles social credit commands and events."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.magic_smoke_votes = {}  # {target_id: {voter_ids}}
        self.supreme_smoke = {
            'target_id': None,
            'target_name': None,
            'leader_id': None,
            'votes': set(),
            'expires': None
        }
        # Track processed interactions to prevent duplicates
        self._processed_interactions = set()
        self._max_tracked_interactions = 1000  # Prevent memory leak

    def _check_duplicate(self, interaction_id: int) -> bool:
        """Check if this interaction was already processed. Returns True if duplicate."""
        if interaction_id in self._processed_interactions:
            return True
        # Clean up old interactions if too many tracked
        if len(self._processed_interactions) >= self._max_tracked_interactions:
            # Remove oldest half
            to_remove = list(self._processed_interactions)[:self._max_tracked_interactions // 2]
            for item in to_remove:
                self._processed_interactions.discard(item)
        self._processed_interactions.add(interaction_id)
        return False

    @app_commands.command(name="thank", description="Thank someone for helping you (+5 credits to them)")
    @app_commands.describe(
        user="The person who helped you",
        reason="What did they help with?"
    )
    async def thank(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "being awesome"
    ):
        """Give someone credit for helping."""
        # Prevent duplicate processing
        if self._check_duplicate(interaction.id):
            return

        # Can't thank yourself
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "😅 You can't thank yourself!",
                ephemeral=True
            )
            return

        # Can't thank bots
        if user.bot:
            await interaction.response.send_message(
                "🤖 Bots don't need social credit!",
                ephemeral=True
            )
            return

        # Add credits
        database.add_credits(
            str(user.id), user.name,
            config.CREDITS['helping_others'],
            f"Thanked by {interaction.user.name}: {reason}"
        )

        embed = discord.Embed(
            title="🙏 Thank You!",
            description=(
                f"{interaction.user.mention} thanked {user.mention} for:\n"
                f"*\"{reason}\"*\n\n"
                f"**+{config.CREDITS['helping_others']}** social credits!"
            ),
            color=discord.Color.gold()
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="documented", description="Log that you documented something (+3 credits)")
    @app_commands.describe(description="What did you document?")
    async def documented(self, interaction: discord.Interaction, description: str):
        """Award credits for documentation."""
        # Prevent duplicate processing
        if self._check_duplicate(interaction.id):
            return

        database.add_credits(
            str(interaction.user.id), interaction.user.name,
            config.CREDITS['documentation'],
            f"Documentation: {description}"
        )

        embed = discord.Embed(
            title="📝 Documentation Hero!",
            description=(
                f"{interaction.user.mention} documented:\n"
                f"*\"{description}\"*\n\n"
                f"**+{config.CREDITS['documentation']}** social credits!"
            ),
            color=discord.Color.blue()
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="magic-smoke", description="Vote that someone released the magic smoke (-10 credits if 3+ votes)")
    @app_commands.describe(user="Who let out the magic smoke?")
    async def magic_smoke(self, interaction: discord.Interaction, user: discord.Member):
        """Vote for magic smoke penalty."""
        # Prevent duplicate processing
        if self._check_duplicate(interaction.id):
            return

        # Can't vote for yourself
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "🤔 You can't vote against yourself!",
                ephemeral=True
            )
            return

        # Can't vote for bots
        if user.bot:
            await interaction.response.send_message(
                "🤖 Bots can't release magic smoke!",
                ephemeral=True
            )
            return

        # Check if already voted
        if database.has_voted_magic_smoke(str(user.id), str(interaction.user.id)):
            # Get current voters to show who has voted
            voter_ids = database.get_magic_smoke_voters(str(user.id))
            voter_mentions = [f"<@{vid}>" for vid in voter_ids]
            await interaction.response.send_message(
                f"❌ You've already voted against {user.mention}!\n\n"
                f"**Current votes ({len(voter_ids)}/{config.MAGIC_SMOKE_VOTES_REQUIRED}):** {', '.join(voter_mentions)}",
                ephemeral=True
            )
            return

        # Add vote
        votes = database.add_magic_smoke_vote(str(user.id), str(interaction.user.id))

        if votes >= config.MAGIC_SMOKE_VOTES_REQUIRED:
            # Apply penalty
            database.add_credits(
                str(user.id), user.name,
                config.CREDITS['magic_smoke'],
                "Released the magic smoke! 💨"
            )
            database.apply_magic_smoke(str(user.id))

            embed = discord.Embed(
                title="💨 MAGIC SMOKE RELEASED!",
                description=(
                    f"The votes are in! {user.mention} has officially "
                    f"released the magic smoke!\n\n"
                    f"**{config.CREDITS['magic_smoke']}** social credits!"
                ),
                color=discord.Color.dark_red()
            )
            await interaction.response.send_message(embed=embed)
        else:
            remaining = config.MAGIC_SMOKE_VOTES_REQUIRED - votes
            # Get all voters to show who has voted
            voter_ids = database.get_magic_smoke_voters(str(user.id))
            voter_mentions = [f"<@{vid}>" for vid in voter_ids]
            await interaction.response.send_message(
                f"💨 {interaction.user.mention} voted against {user.mention}!\n"
                f"**{votes}/{config.MAGIC_SMOKE_VOTES_REQUIRED}** votes "
                f"({remaining} more needed)\n\n"
                f"**Voted:** {', '.join(voter_mentions)}\n\n"
                f"Use `/magic-smoke @{user.name}` to add your vote!"
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Track meme posts and notebook posts for credit."""
        # Ignore bots
        if message.author.bot:
            return

        # Check if memes channel
        if config.MEMES_CHANNEL_ID and message.channel.id == config.MEMES_CHANNEL_ID:
            # Check if user can earn meme credit today
            if database.can_earn_meme_credit(str(message.author.id)):
                database.add_credits(
                    str(message.author.id), message.author.name,
                    config.CREDITS['meme_post'],
                    "Meme post"
                )
                database.record_meme_credit(str(message.author.id))

                # React to acknowledge
                try:
                    await message.add_reaction("🎭")
                except:
                    pass

        # Check if notebooking channel with images/attachments
        if config.NOTEBOOKING_CHANNEL_ID and message.channel.id == config.NOTEBOOKING_CHANNEL_ID:
            # Check if message has images or attachments
            has_media = len(message.attachments) > 0 or len(message.embeds) > 0
            if has_media:
                # Track for voting instead of giving immediate points
                database.track_notebook_submission(str(message.id), str(message.author.id))

                # Add voting reactions
                try:
                    await message.add_reaction(config.NOTEBOOK_UPVOTE_EMOJI)
                    await message.add_reaction(config.NOTEBOOK_DOWNVOTE_EMOJI)

                    # Send info message
                    await message.reply(
                        f"📝 **Notebook Entry Submitted!**\n"
                        f"Vote with {config.NOTEBOOK_UPVOTE_EMOJI} or {config.NOTEBOOK_DOWNVOTE_EMOJI}\n"
                        f"• {config.NOTEBOOK_VOTES_REQUIRED}+ net upvotes = **+{config.CREDITS['documentation']} credits**\n"
                        f"• Net downvotes = **{config.NOTEBOOK_DOWNVOTE_PENALTY} credits**",
                        delete_after=30  # Auto-delete after 30 seconds
                    )
                except:
                    pass

        # Track message for potential roasting (but not check-in channel)
        if message.channel.id != config.CHECKIN_CHANNEL_ID:
            database.track_roasted_message(str(message.id), str(message.author.id))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Track reactions for roasting and notebook voting."""
        emoji = str(payload.emoji)

        # Ignore bot reactions
        if payload.user_id == self.bot.user.id:
            return

        # Get the channel and message
        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except:
            return

        # Handle notebook voting
        if emoji in [config.NOTEBOOK_UPVOTE_EMOJI, config.NOTEBOOK_DOWNVOTE_EMOJI]:
            submission = database.get_notebook_submission(str(message.id))
            if submission and not submission['resolved']:
                # Can't vote on your own submission
                if str(payload.user_id) == submission['discord_id']:
                    return

                # Count votes
                upvotes = 0
                downvotes = 0
                for reaction in message.reactions:
                    if str(reaction.emoji) == config.NOTEBOOK_UPVOTE_EMOJI:
                        upvotes = reaction.count - 1  # Subtract bot's reaction
                    elif str(reaction.emoji) == config.NOTEBOOK_DOWNVOTE_EMOJI:
                        downvotes = reaction.count - 1  # Subtract bot's reaction

                net_votes = upvotes - downvotes

                # Check if enough votes to resolve
                if net_votes >= config.NOTEBOOK_VOTES_REQUIRED:
                    # Approved! Give points
                    try:
                        user = await self.bot.fetch_user(int(submission['discord_id']))
                        database.add_credits(
                            submission['discord_id'], user.name,
                            config.CREDITS['documentation'],
                            f"Notebook entry approved! ({upvotes} upvotes)"
                        )
                        database.resolve_notebook_submission(str(message.id), 'approved')

                        await message.reply(
                            f"✅ **Notebook Entry Approved!**\n"
                            f"{user.mention} earned **+{config.CREDITS['documentation']}** credits!\n"
                            f"Votes: {upvotes} 👍 / {downvotes} 👎"
                        )
                    except:
                        pass

                elif net_votes <= -1:  # Net negative
                    # Rejected! Deduct points
                    try:
                        user = await self.bot.fetch_user(int(submission['discord_id']))
                        database.add_credits(
                            submission['discord_id'], user.name,
                            config.NOTEBOOK_DOWNVOTE_PENALTY,
                            f"Notebook entry rejected ({downvotes} downvotes)"
                        )
                        database.resolve_notebook_submission(str(message.id), 'rejected')

                        await message.reply(
                            f"❌ **Notebook Entry Rejected!**\n"
                            f"{user.mention} lost **{config.NOTEBOOK_DOWNVOTE_PENALTY}** credits\n"
                            f"Votes: {upvotes} 👍 / {downvotes} 👎"
                        )
                    except:
                        pass

            return

        # Handle roasting (fire emoji)
        if emoji != config.ROAST_EMOJI:
            return

        # Count fire reactions
        fire_count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == config.ROAST_EMOJI:
                fire_count = reaction.count
                break

        # Check if threshold reached and not already roasted
        if fire_count >= config.ROAST_THRESHOLD:
            if database.is_message_roasted(str(message.id)):
                return

            # Apply roast penalty
            author_id = database.get_roasted_message_author(str(message.id))
            if author_id and author_id != str(self.bot.user.id):
                # Fetch user info
                try:
                    user = await self.bot.fetch_user(int(author_id))
                    database.add_credits(
                        author_id, user.name,
                        config.CREDITS['roasted'],
                        f"Got roasted! 🔥"
                    )
                    database.mark_message_roasted(str(message.id))

                    # Reply to message
                    await message.reply(
                        f"🔥 {user.mention} got **ROASTED**! "
                        f"({config.CREDITS['roasted']} social credit)"
                    )
                except:
                    pass

    def _is_supreme_leader(self, member: discord.Member) -> bool:
        """Check if member has the Supreme Leader role."""
        role = discord.utils.get(member.roles, name=config.WINNER_ROLE_NAME)
        return role is not None

    @app_commands.command(name="supreme-smoke", description="[Supreme Leader] Nominate someone for magic smoke punishment")
    @app_commands.describe(user="Who deserves the magic smoke?")
    async def supreme_smoke(self, interaction: discord.Interaction, user: discord.Member):
        """Supreme Leader nominates someone for magic smoke."""
        # Prevent duplicate processing
        if self._check_duplicate(interaction.id):
            return

        # Check if user is Supreme Leader
        if not self._is_supreme_leader(interaction.user):
            await interaction.response.send_message(
                "👑 Only the **Supreme Leader** can use this command!",
                ephemeral=True
            )
            return

        # Can't target yourself
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "😅 You can't nominate yourself!",
                ephemeral=True
            )
            return

        # Can't target bots
        if user.bot:
            await interaction.response.send_message(
                "🤖 Bots can't be nominated!",
                ephemeral=True
            )
            return

        # Set up the nomination
        self.supreme_smoke = {
            'target_id': str(user.id),
            'target_name': user.name,
            'leader_id': str(interaction.user.id),
            'votes': set(),
            'expires': datetime.now() + timedelta(hours=24)
        }

        embed = discord.Embed(
            title="⚡ SUPREME LEADER'S JUDGMENT ⚡",
            description=(
                f"👑 {interaction.user.mention} has nominated {user.mention} "
                f"for **Magic Smoke** punishment!\n\n"
                f"**{config.CREDITS['magic_smoke']}** credits at stake!\n\n"
                f"Use `/agree-smoke` to support this judgment.\n"
                f"**2 votes needed** to execute the punishment.\n\n"
                f"*Expires in 24 hours*"
            ),
            color=discord.Color.dark_gold()
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="agree-smoke", description="Agree with the Supreme Leader's magic smoke nomination")
    async def agree_smoke(self, interaction: discord.Interaction):
        """Vote to support Supreme Leader's nomination."""
        # Prevent duplicate processing
        if self._check_duplicate(interaction.id):
            return

        # Check if there's an active nomination
        if not self.supreme_smoke['target_id']:
            await interaction.response.send_message(
                "❌ No active Supreme Leader nomination!",
                ephemeral=True
            )
            return

        # Check if expired
        if datetime.now() > self.supreme_smoke['expires']:
            self.supreme_smoke = {
                'target_id': None,
                'target_name': None,
                'leader_id': None,
                'votes': set(),
                'expires': None
            }
            await interaction.response.send_message(
                "⏰ The nomination has expired!",
                ephemeral=True
            )
            return

        # Can't be the Supreme Leader voting
        if str(interaction.user.id) == self.supreme_smoke['leader_id']:
            await interaction.response.send_message(
                "👑 As Supreme Leader, your nomination already counts!",
                ephemeral=True
            )
            return

        # Can't be the target
        if str(interaction.user.id) == self.supreme_smoke['target_id']:
            await interaction.response.send_message(
                "😅 You can't vote on your own punishment!",
                ephemeral=True
            )
            return

        # Add vote
        self.supreme_smoke['votes'].add(str(interaction.user.id))
        vote_count = len(self.supreme_smoke['votes'])

        if vote_count >= 2:
            # Execute the punishment!
            target_id = self.supreme_smoke['target_id']
            target_name = self.supreme_smoke['target_name']

            database.add_credits(
                target_id, target_name,
                config.CREDITS['magic_smoke'],
                "Supreme Leader's Judgment! 👑💨"
            )

            embed = discord.Embed(
                title="💨 SUPREME JUDGMENT EXECUTED! 💨",
                description=(
                    f"The people have spoken!\n\n"
                    f"<@{target_id}> has been punished by the Supreme Leader's decree!\n\n"
                    f"**{config.CREDITS['magic_smoke']}** social credits!"
                ),
                color=discord.Color.dark_red()
            )

            # Reset
            self.supreme_smoke = {
                'target_id': None,
                'target_name': None,
                'leader_id': None,
                'votes': set(),
                'expires': None
            }

            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f"✅ {interaction.user.mention} supports the judgment! **{vote_count}/2** votes to execute.\n"
                f"Target: **{self.supreme_smoke['target_name']}**\n"
                f"Use `/agree-smoke` to add your vote!"
            )

    @app_commands.command(name="remove-credits", description="[Admin] Remove credits from a user")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        user="The user to remove credits from",
        amount="How many credits to remove (positive number)",
        reason="Reason for removing credits"
    )
    async def remove_credits(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: int,
        reason: str = "Admin adjustment"
    ):
        """Manually remove credits from a user."""
        # Prevent duplicate processing
        if self._check_duplicate(interaction.id):
            return

        # Ensure amount is positive
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Amount must be a positive number!",
                ephemeral=True
            )
            return

        # Can't remove from bots
        if user.bot:
            await interaction.response.send_message(
                "🤖 Bots don't have social credit!",
                ephemeral=True
            )
            return

        # Remove credits (add negative amount)
        database.add_credits(
            str(user.id), user.name,
            -amount,
            f"Admin removal by {interaction.user.name}: {reason}"
        )

        embed = discord.Embed(
            title="⚖️ Credits Removed",
            description=(
                f"{interaction.user.mention} removed credits from {user.mention}\n\n"
                f"**-{amount}** social credits\n"
                f"Reason: *{reason}*"
            ),
            color=discord.Color.red()
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="add-credits", description="[Admin] Add credits to a user")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        user="The user to add credits to",
        amount="How many credits to add (positive number)",
        reason="Reason for adding credits"
    )
    async def add_credits_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: int,
        reason: str = "Admin adjustment"
    ):
        """Manually add credits to a user."""
        # Prevent duplicate processing
        if self._check_duplicate(interaction.id):
            return

        # Ensure amount is positive
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Amount must be a positive number!",
                ephemeral=True
            )
            return

        # Can't add to bots
        if user.bot:
            await interaction.response.send_message(
                "🤖 Bots don't need social credit!",
                ephemeral=True
            )
            return

        # Add credits
        database.add_credits(
            str(user.id), user.name,
            amount,
            f"Admin grant by {interaction.user.name}: {reason}"
        )

        embed = discord.Embed(
            title="✨ Credits Added",
            description=(
                f"{interaction.user.mention} added credits to {user.mention}\n\n"
                f"**+{amount}** social credits\n"
                f"Reason: *{reason}*"
            ),
            color=discord.Color.green()
        )

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SocialCreditCog(bot))
