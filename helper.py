# helper.py
# This file contains the implementation for many of the bot's commands and event handlers.
# It is designed to keep the main `bot.py` file cleaner and more focused on the core bot structure.

import asyncio
import discord
import math
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional, Union, List, Tuple

from discord.ext import commands
from discord.ui import View, Button
from loguru import logger

# Import shared tools and data structures
from tools import (
    BotState,
    BotConfig,
    get_discord_age,
    record_command_usage,
    record_command_usage_by_user,
    handle_errors,
    format_duration,
)

def format_departure_time(duration: timedelta) -> str:
    """
    Formats the duration of a member's stay into a human-readable string (e.g., '1y 2d 3h').
    This calls the universal format_duration function from tools.
    """
    return format_duration(duration)


def create_message_chunks(
    entries: List[Any],
    title: str,
    process_entry: Callable[[Any], str],
    max_chunk_size: int = 50,
    max_length: int = 4000, # Increased for embed descriptions
    as_embed: bool = False,
    embed_color: Optional[discord.Color] = None
) -> Union[List[str], List[discord.Embed]]:
    """
    A utility function to split a long list of text entries into multiple messages or embeds.
    This is essential for avoiding Discord's character limits.
    """
    if as_embed and embed_color is None:
        raise ValueError("embed_color must be provided when as_embed=True")

    chunks = []
    current_chunk = []
    current_length = 0

    # Embeds don't need a separate title string in the content
    title_length = 0 if as_embed else len(f"**{title} ({len(entries)} total)**\n")

    for entry in entries:
        processed_list = process_entry(entry)
        # Ensure process_entry always returns a list of strings
        if not isinstance(processed_list, list):
            processed_list = [processed_list]

        for processed in processed_list:
            if processed:
                entry_length = len(processed) + 1  # +1 for the newline

                if (current_length + entry_length > max_length and current_chunk) or \
                   (len(current_chunk) >= max_chunk_size):
                    if as_embed:
                        embed = discord.Embed(title=title, description="\n".join(current_chunk), color=embed_color)
                        chunks.append(embed)
                    else:
                        chunks.append(f"**{title} ({len(entries)} total)**\n" + "\n".join(current_chunk))
                    current_chunk = []
                    current_length = 0

                current_chunk.append(processed)
                current_length += entry_length

    if current_chunk:
        if as_embed:
            embed = discord.Embed(title=title, description="\n".join(current_chunk), color=embed_color)
            chunks.append(embed)
        else:
            chunks.append(f"**{title} ({len(entries)} total)**\n" + "\n".join(current_chunk))

    return chunks

async def _button_callback_handler(interaction: discord.Interaction, command: str, bot_config: BotConfig, state: BotState) -> None:
    """A generic handler for button presses, including permissions and cooldowns."""
    try:
        user_id = interaction.user.id
        # Permission Check (Channel)
        if user_id not in bot_config.ALLOWED_USERS and interaction.channel.id != bot_config.COMMAND_CHANNEL_ID:
            await interaction.response.send_message(f"All commands should be used in <#{bot_config.COMMAND_CHANNEL_ID}>", ephemeral=True)
            return

        # Cooldown Check
        current_time = time.time()
        async with state.cooldown_lock:
            if user_id in state.button_cooldowns:
                last_used, warned = state.button_cooldowns[user_id]
                time_left = bot_config.COMMAND_COOLDOWN - (current_time - last_used)
                if time_left > 0:
                    if not warned:
                        await interaction.response.send_message(f"{interaction.user.mention}, wait {int(time_left)}s before using another button.", ephemeral=True)
                        state.button_cooldowns[user_id] = (last_used, True)
                    else:
                        # If already warned, just defer silently
                        try:
                            await interaction.response.defer(ephemeral=True, thinking=False)
                        except discord.InteractionResponded:
                            pass # Already deferred, maybe by another process
                    return
            # Update cooldown timestamp
            state.button_cooldowns[user_id] = (current_time, False)

        # Defer first (ephemeral so only user sees "Thinking...")
        try:
            await interaction.response.defer(ephemeral=True, thinking=False)
        except discord.InteractionResponded:
             logger.warning("Interaction already responded to before deferral in _button_callback_handler.")
             pass # Interaction might have already been deferred or responded to


        # --- NEW: Send public announcement message ---
        try:
            # Construct the announcement message
            announcement_content = f"{interaction.user.mention} used `{command}`"
            # Send it to the same channel the button was clicked in
            await interaction.channel.send(announcement_content)
            logger.info(f"Announced button use: {interaction.user.name} used {command}")
        except discord.Forbidden:
             logger.warning(f"Missing permissions to send announcement message in #{interaction.channel.name}")
        except Exception as e:
             logger.error(f"Failed to send button usage announcement: {e}")
        # --- END NEW ---


        # Find and Invoke Command
        cmd_name = command.lstrip("!")
        command_obj = interaction.client.get_command(cmd_name)
        if command_obj:
            # Create a fake message object that mimics a real command invocation
            # Send it temporarily so context can be created, then delete it.
            fake_message = None
            try:
                fake_message = await interaction.channel.send(f"Processing {command} for {interaction.user.mention}...")
                fake_message.content = command
                fake_message.author = interaction.user

                # Create a new context from the fake message and invoke the command
                ctx = await interaction.client.get_context(fake_message)
                await interaction.client.invoke(ctx)

            except Exception as invoke_err:
                 logger.error(f"Error invoking command '{cmd_name}' from button: {invoke_err}", exc_info=True)
                 # Try to send an ephemeral follow-up if possible
                 try:
                     await interaction.followup.send("An error occurred while running that command.", ephemeral=True)
                 except Exception:
                     pass # Ignore if followup fails
            finally:
                # Delete the fake message to keep the channel clean
                if fake_message:
                    try:
                        await fake_message.delete()
                    except discord.NotFound:
                        pass # Already deleted
                    except discord.Forbidden:
                        logger.warning(f"Missing permissions to delete fake message in #{interaction.channel.name}")
        else:
            logger.warning(f"Button tried to invoke non-existent command: {cmd_name}")
            try:
                await interaction.followup.send("Could not process that command.", ephemeral=True)
            except Exception:
                 pass # Ignore if followup fails

    except Exception as e:
        # General error handler for the whole callback
        logger.error(f"Error in button callback: {e}", exc_info=True)
        try:
             # Check if we can still send an ephemeral follow-up
             if interaction.response.is_done():
                 await interaction.followup.send("An error occurred processing the button click.", ephemeral=True)
             else:
                 # If not done, try the initial response
                 await interaction.response.send_message("An error occurred processing the button click.", ephemeral=True)
        except Exception as final_err:
             logger.error(f"Failed to send final error message for button callback: {final_err}")


class HelpButton(Button):
    def __init__(self, label: str, emoji: str, command: str, style: discord.ButtonStyle, bot_config: BotConfig, state: BotState):
        super().__init__(label=label, emoji=emoji, style=style)
        self.command, self.bot_config, self.state = command, bot_config, state
    async def callback(self, interaction: discord.Interaction): await _button_callback_handler(interaction, self.command, self.bot_config, self.state)

class MusicButton(Button):
    def __init__(self, label: str, emoji: str, command: str, style: discord.ButtonStyle, bot_config: BotConfig, state: BotState):
        super().__init__(label=label, emoji=emoji, style=style)
        self.command, self.bot_config, self.state = command, bot_config, state
    async def callback(self, interaction: discord.Interaction): await _button_callback_handler(interaction, self.command, self.bot_config, self.state)

class HelpView(View):
    def __init__(self, bot_config: BotConfig, state: BotState):
        super().__init__(timeout=None)
        # Define buttons with their emoji, label, command, and desired color style
        cmds = [
            ("⏸️", "👤", "!refresh", discord.ButtonStyle.danger),
            ("⏭️", "👤", "!skip", discord.ButtonStyle.success),
            ("ℹ️", "👤", "!info", discord.ButtonStyle.primary),
            ("🚩", "👤", "!report", discord.ButtonStyle.secondary)
        ]
        for e, l, c, s in cmds:
            self.add_item(HelpButton(label=l, emoji=e, command=c, style=s, bot_config=bot_config, state=state))

# --- Interactive Queue Components ---
class QueueDropdown(discord.ui.Select):
    def __init__(self, bot, state, page_items, author):
        self.bot = bot
        self.state = state
        self.author = author

        options = [
            discord.SelectOption(label=f"{i + 1}. {song_info.get('title', 'Unknown Title')}"[:100], value=str(i))
            for i, song_info in page_items
        ]

        super().__init__(placeholder="Select a song to jump to...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Check if the interacting user is the one who initiated the command.
        if interaction.user != self.author:
            await interaction.response.send_message("You can't control this menu.", ephemeral=True)
            return

        selected_index = int(self.values[0])

        async with self.state.music_lock:
            full_queue = self.state.active_playlist + self.state.search_queue
            if selected_index >= len(full_queue):
                await interaction.response.send_message("That song is no longer in the queue. The list may be outdated.", ephemeral=True, delete_after=10)
                try:
                    await interaction.message.delete()
                except discord.NotFound:
                    pass
                return

            # Get the selected song and remove it from its original position in the full queue list
            selected_song = full_queue.pop(selected_index)

            # Reconstruct the original queues from the modified full queue
            len_active = len(self.state.active_playlist)
            if selected_index < len_active:
                self.state.active_playlist.pop(selected_index)
            else:
                self.state.search_queue.pop(selected_index - len_active)

            # Place the selected song at the front of the search queue to be played next
            self.state.search_queue.insert(0, selected_song)
            # Set the override flag to ensure this song plays next, regardless of mode.
            self.state.play_next_override = True

        # Stop the current song to trigger the `after` callback, which will play the new song.
        if self.bot.voice_client_music and self.bot.voice_client_music.is_connected():
            self.bot.voice_client_music.stop()
            await interaction.response.send_message(f"✅ Jumping to **{selected_song.get('title')}**.", ephemeral=True, delete_after=10)
        else:
            await interaction.response.send_message(f"✅ Queued **{selected_song.get('title')}** to play next.", ephemeral=True, delete_after=10)

        # After responding, delete the original message with the queue menu.
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass # The message might have been deleted already.


class QueueView(discord.ui.View):
    def __init__(self, bot, state, author):
        super().__init__(timeout=300.0)
        self.bot = bot
        self.state = state
        self.author = author
        self.current_page = 0
        self.page_size = 25
        self.full_queue = []
        self.message = None

    async def start(self):
        await self.update_queue()
        self.update_components()

    async def update_queue(self):
        async with self.state.music_lock:
            self.full_queue = list(enumerate(self.state.active_playlist + self.state.search_queue))
        self.total_pages = (len(self.full_queue) + self.page_size - 1) // self.page_size
        self.total_pages = max(1, self.total_pages)

    def get_content(self) -> str:
        """Generates the content string for the queue message."""
        total_songs = len(self.full_queue)
        page_num = self.current_page + 1
        return f"**Current Queue ({total_songs} songs):** Page {page_num}/{self.total_pages}\n*(Select a song to jump to it)*"

    def update_components(self):
        self.clear_items()

        start_index = self.current_page * self.page_size
        end_index = start_index + self.page_size
        page_items = self.full_queue[start_index:end_index]

        if page_items:
            self.add_item(QueueDropdown(self.bot, self.state, page_items, self.author))

        if self.total_pages > 1:
            self.add_item(self.create_nav_button("⬅️ Prev", "prev_page", self.current_page == 0))
            self.add_item(self.create_nav_button("Next ➡️", "next_page", self.current_page >= self.total_pages - 1))

    def create_nav_button(self, label: str, custom_id: str, disabled: bool) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=custom_id, disabled=disabled)

        async def nav_callback(interaction: discord.Interaction):
            if interaction.user != self.author:
                await interaction.response.send_message("You can't control this menu.", ephemeral=True)
                return

            if interaction.data['custom_id'] == 'prev_page':
                self.current_page -= 1
            elif interaction.data['custom_id'] == 'next_page':
                self.current_page += 1

            self.update_components()
            await interaction.response.edit_message(content=self.get_content(), view=self)

        button.callback = nav_callback
        return button

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass


class MusicView(discord.ui.View):
    def __init__(self, bot_config: BotConfig, state: BotState):
        super().__init__(timeout=None)
        btns = [
            ("⏯️", "🎵", "!mpauseplay", discord.ButtonStyle.danger),
            ("⏭️", "🎵", "!mskip", discord.ButtonStyle.success),
            ("🔀", "🎵", "!mshuffle", discord.ButtonStyle.primary),
            ("❌", "🎵", "!mclear", discord.ButtonStyle.secondary)
        ]
        for e, l, c, s in btns:
            self.add_item(MusicButton(label=l, emoji=e, command=c, style=s, bot_config=bot_config, state=state))

class BotHelper:
    """
    A class that encapsulates the logic for various bot commands and event notifications.
    This promotes modularity by separating command implementation from the event listeners in `bot.py`.
    """
    def __init__(self, bot: commands.Bot, state: BotState, bot_config: BotConfig, save_func: Optional[Callable] = None, play_next_song_func: Optional[Callable] = None):
        self.bot = bot
        self.state = state
        self.bot_config = bot_config
        self.save_state = save_func
        self.play_next_song = play_next_song_func
        self.LEAVE_BATCH_DELAY_SECONDS = 10

    async def _schedule_leave_processing(self):
        """Waits for a set delay, then processes the leave buffer."""
        await asyncio.sleep(self.LEAVE_BATCH_DELAY_SECONDS)
        await self._process_leave_batch()

    async def _process_leave_batch(self):
        """Processes the buffered members and sends a single summary message with details."""
        async with self.state.moderation_lock:
            if not self.state.leave_buffer:
                return # Nothing to do

            # Make a copy of the buffered data and clear the buffer
            members_to_announce = self.state.leave_buffer.copy()
            self.state.leave_buffer.clear()
            self.state.leave_batch_task = None

        chat_channel = self.bot.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel:
            return

        count = len(members_to_announce)

        # Handle a single departure with a detailed embed
        if count == 1:
            member_data = members_to_announce[0]
            embed = discord.Embed(
                description=f"{member_data['mention']} **LEFT the SERVER**",
                color=discord.Color.red()
            )
            embed.set_author(name=member_data['name'], icon_url=member_data['avatar_url'])

            # Add the duration the member was in the server
            if member_data['joined_at']:
                duration = datetime.now(timezone.utc) - member_data['joined_at']
                duration_str = format_departure_time(duration)
                embed.add_field(name="Time in Server", value=duration_str, inline=True)

            # Add the roles the member had
            embed.add_field(name="Roles", value=member_data['roles'], inline=True)

        # Handle a mass departure with a summary embed
        else:
            embed = discord.Embed(
                title=f"🚪 Mass Departure Event",
                color=discord.Color.red()
            )
            description_lines = []
            # List the first 10 members with their stay duration
            for member_data in members_to_announce[:10]:
                duration_str = ""
                if member_data['joined_at']:
                    duration = datetime.now(timezone.utc) - member_data['joined_at']
                    # Use the helper function for consistent formatting
                    duration_str = f" (Stayed for {format_departure_time(duration)})"

                description_lines.append(f"• {member_data['name']}{duration_str}")

            description = "\n".join(description_lines)

            # Summarize if more than 10 members left
            if count > 10:
                description += f"\n...and {count - 10} others."

            embed.description = description
            embed.set_footer(text=f"{count} members left the server.")

        if self.state.notifications_enabled:
            await chat_channel.send(embed=embed)

        logger.info(f"Processed a batch of {count} member departures.")

    async def _log_timeout_in_state(self, member: discord.Member, duration_seconds: int, reason: str, moderator_name: str, moderator_id: Optional[int] = None):
        """
        A centralized, thread-safe method for recording a member's timeout information into the bot's state.

        Args:
            member: The member who was timed out.
            duration_seconds: The duration of the timeout in seconds.
            reason: The reason for the timeout.
            moderator_name: The name of the moderator who issued the timeout.
            moderator_id: The ID of the moderator.
        """
        async with self.state.moderation_lock:
            self.state.active_timeouts[member.id] = {
                "timeout_end": time.time() + duration_seconds,
                "reason": reason,
                "timed_by": moderator_name,
                "timed_by_id": moderator_id,
                "start_timestamp": time.time()
            }

    async def _create_departure_embed(self, member_or_user: Union[discord.Member, discord.User], moderator: Union[discord.User, str], reason: str, action: str, color: discord.Color) -> discord.Embed:
        """
        Creates a standardized, rich embed for member departure events like kicks and bans.
        This ensures consistent formatting for all such notifications.

        Args:
            member_or_user: The user/member who departed. Can be a real object or a mock object.
            moderator: The moderator responsible for the action.
            reason: The reason for the departure.
            action: The type of action (e.g., "KICKED", "BANNED").
            color: The color for the embed's side bar.

        Returns:
            A fully constructed discord.Embed object ready to be sent.
        """
        # Handle both real objects and mock (namedtuple) objects
        mention = getattr(member_or_user, 'mention', f"<@{member_or_user.id}>")
        author_name = getattr(member_or_user, 'name', 'Unknown User')
        avatar_url = member_or_user.display_avatar.url if hasattr(member_or_user, 'display_avatar') and member_or_user.display_avatar else None

        # Adjust wording for kicks
        if action.upper() == "KICKED":
            description = f"{mention} **was {action.upper()}**"
        else:
            description = f"{mention} **{action.upper()}**"

        embed = discord.Embed(description=description, color=color)
        if avatar_url:
            embed.set_author(name=author_name, icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)

        # Attempt to fetch the user's banner for a more visually appealing embed.
        try:
            user_obj = await self.bot.fetch_user(member_or_user.id)
            if user_obj.banner:
                embed.set_image(url=user_obj.banner.url)
        except Exception:
            pass  # Ignore if the banner can't be fetched (e.g., user has none).

        moderator_mention = getattr(moderator, 'mention', str(moderator))
        embed.add_field(name="Moderator", value=moderator_mention, inline=True)

        # Add details like time in server and roles if the data is available
        if hasattr(member_or_user, 'joined_at') and member_or_user.joined_at:
            duration = datetime.now(timezone.utc) - member_or_user.joined_at
            duration_str = format_departure_time(duration)
            embed.add_field(name="Time in Server", value=duration_str, inline=True)

        if hasattr(member_or_user, 'roles'):
            # For real members, get mentions. For mock members, the list already contains mentions.
            if isinstance(member_or_user, discord.Member):
                roles = [role.mention for role in member_or_user.roles if role.name != "@everyone"]
            else:
                roles = member_or_user.roles # This is now a list of mention strings

            if roles:
                roles.reverse() # Show highest roles first
                embed.add_field(name="Roles", value=" ".join(roles), inline=True)


        embed.add_field(name="Reason", value=reason, inline=False)
        return embed

    @handle_errors
    async def handle_member_join(self, member: discord.Member) -> None:
        """
        Handles the logic for when a new member joins the server.
        It sends a welcome message and logs the join event.
        """
        if member.guild.id != self.bot_config.GUILD_ID:
            return

        chat_channel = member.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if chat_channel:
            embed = discord.Embed(
                description=f"{member.mention} **JOINED the SERVER**!",
                color=discord.Color.green())

            embed.set_author(name=member.name, icon_url=member.display_avatar.url)
            embed.set_thumbnail(url=member.display_avatar.url)

            try:
                # Fetch the full user object specifically to get banner information
                user_obj = await self.bot.fetch_user(member.id)
                if user_obj and user_obj.banner:
                    embed.set_image(url=user_obj.banner.url)
            except Exception as e:
                 logger.warning(f"Could not fetch banner for new member {member.name}: {e}")

            embed.add_field(
                name="Account Age",
                value=get_discord_age(member.created_at),
                inline=True)

            await chat_channel.send(embed=embed)

        async with self.state.moderation_lock:
            self.state.recent_joins.append((
                member.id,
                member.name,
                member.display_name,
                datetime.now(timezone.utc)
            ))
        logger.info(f"{member.name} joined the server {datetime.now().strftime('%m-%d-%Y %H:%M:%S')}.")

    @handle_errors
    async def send_punishment_vc_notification(self, member: discord.Member, reason: str, moderator_name: str) -> None:
        """
        Sends a rich, formatted notification to the chat channel when a member is moved to the punishment VC.
        """
        chat_channel = member.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel:
            return

        # Create the base embed
        embed = discord.Embed(
            description=f"{member.mention} **was MOVED to the Punishment VC**",
            color=discord.Color.dark_orange())

        embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            # Fetch user to get banner
            user_obj = await self.bot.fetch_user(member.id)
            if user_obj.banner:
                embed.set_image(url=user_obj.banner.url)
        except Exception as e:
            logger.warning(f"Could not fetch user banner for punishment notification: {e}")
            pass

        # Add fields for moderator and reason
        embed.add_field(name="Moved By", value=moderator_name, inline=True)

        final_reason = reason or "No reason provided"
        embed.add_field(name="Reason", value=final_reason, inline=False)

        await chat_channel.send(embed=embed)

    @handle_errors
    async def send_timeout_notification(self, member: discord.Member, moderator: discord.User, duration: int, reason: str = None) -> None:
        """
        Sends a rich, formatted notification to the chat channel when a member is timed out.
        """
        chat_channel = member.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel:
            return

        duration_str = format_duration(duration)

        # Create the base embed
        embed = discord.Embed(
            description=f"{member.mention} **was TIMED OUT**",
            color=discord.Color.orange())

        embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            user_obj = await self.bot.fetch_user(member.id)
            if user_obj.banner:
                embed.set_image(url=user_obj.banner.url)
        except Exception:
            pass

        # --- UPDATED ORDER ---
        embed.add_field(name="Duration", value=duration_str, inline=True)

        roles = [role.mention for role in member.roles if role.name != "@everyone"]
        if roles:
            roles.reverse()
            roles_str = " ".join(roles)
            embed.add_field(name="Roles", value=roles_str, inline=True)
            
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        # --- END UPDATED ORDER ---

        final_reason = reason or "No reason provided"
        embed.add_field(name="Reason", value=final_reason, inline=False)

        await chat_channel.send(embed=embed)

    @handle_errors
    async def send_timeout_removal_notification(self, member: discord.Member, duration: int, reason: str = "Expired Naturally") -> None:
        """
        Sends a rich, formatted notification when a member's timeout is removed or expires.
        """
        if not self.state.notifications_enabled: return
        chat_channel = member.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel: return

        duration_str = format_duration(duration)

        embed = discord.Embed(
            description=f"{member.mention} **TIMEOUT REMOVED**",
            color=discord.Color.orange())

        embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            user_obj = await self.bot.fetch_user(member.id)
            if user_obj.banner: embed.set_image(url=user_obj.banner.url)
        except Exception: pass

        embed.add_field(name="Duration", value=duration_str, inline=True)

        if "manually removed by" in reason.lower() or "Timeout removed by" in reason:
            try:
                parts = reason.rsplit("by", 1)
                reason_text = parts[0].strip()
                mod_name = parts[1].strip().lstrip('🛡️').strip()
                mod_member = discord.utils.find(lambda m: m.name == mod_name or m.display_name == mod_name, member.guild.members)
                mod_display = mod_member.mention if mod_member else mod_name
                reason = f"{reason_text} by {mod_display}"
            except Exception as e:
                logger.warning(f"Error processing moderator name for timeout removal: {e}")

        embed.add_field(name="Reason", value=f"{reason}", inline=False)
        await chat_channel.send(embed=embed)

    @handle_errors
    async def send_unban_notification(self, user: discord.User, moderator: discord.User) -> None:
        """Sends a notification when a user is unbanned."""
        if not self.state.notifications_enabled: return
        chat_channel = self.bot.get_guild(self.bot_config.GUILD_ID).get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if chat_channel:
            embed = discord.Embed(description=f"{user.mention} **UNBANNED**", color=discord.Color.green())
            embed.set_author(name=user.name, icon_url=user.display_avatar.url)
            embed.set_thumbnail(url=user.display_avatar.url)

            try:
                user_obj = await self.bot.fetch_user(user.id)
                if user_obj.banner: embed.set_image(url=user_obj.banner.url)
            except Exception: pass

            embed.add_field(name="Moderator", value=moderator.mention, inline=True)
            await chat_channel.send(embed=embed)

            async with self.state.moderation_lock:
                self.state.recent_unbans.append((
                    user.id, user.name, user.display_name, datetime.now(timezone.utc), moderator.name
                ))
                if len(self.state.recent_unbans) > 100: self.state.recent_unbans.pop(0)

    @handle_errors
    async def handle_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        """
        Handles the on_member_ban event. This now acts as the primary source for ban info.
        """
        if guild.id != self.bot_config.GUILD_ID: return

        chat_channel = guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel: return

        reason, moderator = "No reason provided", "Unknown"
        try:
            # Look for the specific ban action in the audit log
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                if entry.target and entry.target.id == user.id:
                    moderator, reason = entry.user, entry.reason or "No reason provided"
                    break
        except Exception as e:
            logger.error(f"Could not fetch audit log for ban: {e}")

        embed = await self._create_departure_embed(user, moderator, reason, "BANNED", discord.Color.red())
        await chat_channel.send(embed=embed)

        async with self.state.moderation_lock:
            # Mark this user as banned so on_member_remove knows to ignore them
            self.state.recently_banned_ids.add(user.id)
            self.state.recent_bans.append((user.id, user.name, getattr(user, 'display_name', user.name), datetime.now(timezone.utc), reason))

    @handle_errors
    async def handle_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        """Handles the on_member_unban event, finding the moderator from the audit log."""
        if guild.id != self.bot_config.GUILD_ID: return

        await asyncio.sleep(4)
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.unban):
            if entry.target.id == user.id:
                await self.send_unban_notification(user, entry.user)
                return
        logger.warning(f"Unban for {user.name} detected, but audit log entry not found.")
        await self.send_unban_notification(user, self.bot.user)

    @handle_errors
    async def handle_member_remove(self, member: discord.Member) -> None:
        """
        FIX: Handles member departure by deterministically checking for a ban or kick first,
        then processing as a leave. This removes the old race condition.
        """
        if member.guild.id != self.bot_config.GUILD_ID: return

        guild = member.guild
        chat_channel = guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel: return

        # Give the on_member_ban event a moment to fire and add the user to the banned set
        await asyncio.sleep(2)

        async with self.state.moderation_lock:
            if member.id in self.state.recently_banned_ids:
                # This departure was a ban and was already handled by on_member_ban.
                self.state.recently_banned_ids.remove(member.id) # Clean up the entry
                logger.info(f"Departure of {member.name} confirmed as a ban, skipping further processing.")
                return

        # If it wasn't a ban, check if it was a kick.
        try:
            # Check the audit log for a kick within the last 30 seconds.
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.kick, after=datetime.now(timezone.utc) - timedelta(seconds=30)):
                if entry.target and entry.target.id == member.id:
                    reason = entry.reason or "No reason provided"
                    embed = await self._create_departure_embed(member, entry.user, reason, "KICKED", discord.Color.orange())
                    if self.state.notifications_enabled:
                        await chat_channel.send(embed=embed)
                    logger.info(f"Processed departure for {member.name} as a KICK.")
                    async with self.state.moderation_lock:
                        roles = [role.mention for role in member.roles if role.name != "@everyone"]
                        self.state.recent_kicks.append((member.id, member.name, member.display_name, datetime.now(timezone.utc), reason, entry.user.mention, " ".join(roles)))
                    return # Kick handled, we are done.
        except discord.Forbidden:
            logger.warning("Missing permissions to check audit log for kicks.")
        except Exception as e:
            logger.error(f"Error checking audit log for kick: {e}")

        # It's a leave, so buffer the data for batch processing.
        logger.info(f"Buffering LEAVE for {member.name}.")

        # Extract all necessary information *before* the member object becomes invalid.
        roles = [role.mention for role in member.roles if role.name != "@everyone"]
        roles.reverse() # Show highest roles first
        role_string = " ".join(roles) if roles else "No roles"

        # The data that will be used to generate the leave notification.
        leave_data_for_notification = {
            'mention': member.mention,
            'name': member.name,
            'avatar_url': member.display_avatar.url,
            'joined_at': member.joined_at,
            'roles': role_string
        }

        async with self.state.moderation_lock:
            # Add to the permanent history log for !whois
            self.state.recent_leaves.append((member.id, member.name, member.display_name, datetime.now(timezone.utc), role_string))

            # Cancel any existing batch task to reset the timer
            if self.state.leave_batch_task:
                self.state.leave_batch_task.cancel()

            # Add the extracted data to the notification buffer
            self.state.leave_buffer.append(leave_data_for_notification)

            # Schedule the new batch processing task
            self.state.leave_batch_task = asyncio.create_task(self._schedule_leave_processing())

    async def send_help_menu(self, target: Any) -> None:
        """Sends the main interactive help menu embed with buttons."""
        try:
            help_description = """
**Pause** --------- Pause Omegle
**Skip** ----------- Skip/Start Omegle
**Report** -------- Report Current User
**Top 10** --------- Top 10 VC Times
**!commands** --- List All Commands
"""
            embed = discord.Embed(title="👤  Omegle Controls  👤", description=help_description, color=discord.Color.blue())
            destination = target.channel if hasattr(target, 'channel') else target
            if destination and hasattr(destination, 'send'):
                await destination.send(embed=embed, view=HelpView(self.bot_config, self.state))
        except Exception as e:
            logger.error(f"Error in send_help_menu: {e}", exc_info=True)

    @handle_errors
    async def show_bans(self, ctx) -> None:
        """(Command) Lists all banned users in the server."""
        record_command_usage(self.state.analytics, "!bans")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!bans")

        ban_entries = [entry async for entry in ctx.guild.bans()]
        if not ban_entries:
            await ctx.send("No users are currently banned.")
            return

        def process_ban(entry):
            user = entry.user
            reason = entry.reason or "No reason provided"
            return f"• `{user.name}` (`{user.id}`) | Reason: *{reason}*"

        embeds = create_message_chunks(
            entries=ban_entries,
            title=f"Banned Users (Total: {len(ban_entries)})",
            process_entry=process_ban,
            as_embed=True,
            embed_color=discord.Color.red()
        )

        for embed in embeds: await ctx.send(embed=embed)

    @handle_errors
    async def show_top_members(self, ctx) -> None:
        """(Command) Lists the top 10 oldest server members and top 10 oldest Discord accounts."""
        await ctx.send("Gathering member data, this may take a moment...")

        members = list(ctx.guild.members)
        joined_members = sorted([m for m in members if m.joined_at], key=lambda m: m.joined_at)[:10]
        created_members = sorted(members, key=lambda m: m.created_at)[:10]

        async def create_member_embed(member, rank, color, show_join_date=True):
            user_obj = member
            try:
                # Fetch full user object to get banner, but don't fail if it doesn't work
                fetched_user = await self.bot.fetch_user(member.id)
                if fetched_user:
                    user_obj = fetched_user
            except Exception:
                pass # Fallback to the member object if fetch fails

            embed = discord.Embed(title=f"#{rank} - {member.display_name}", description=f"{member.mention}", color=color)
            embed.set_author(name=f"{member.name}#{member.discriminator}", icon_url=member.display_avatar.url)
            embed.set_thumbnail(url=member.display_avatar.url)
            if hasattr(user_obj, 'banner') and user_obj.banner:
                embed.set_image(url=user_obj.banner.url)

            embed.add_field(name="Account Created", value=f"{member.created_at.strftime('%m-%d-%Y')}\n({get_discord_age(member.created_at)} old)", inline=True)
            if show_join_date and member.joined_at:
                embed.add_field(name="Joined Server", value=f"{member.joined_at.strftime('%m-%d-%Y')}\n({get_discord_age(member.joined_at)} ago)", inline=True)

            roles = [role.mention for role in member.roles if role.name != "@everyone"]
            if roles:
                role_str = " ".join(roles)
                if len(role_str) > 1024:
                    role_str = "Too many roles to display."
                embed.add_field(name=f"Roles ({len(roles)})", value=role_str, inline=False)

            return embed

        await ctx.send("**🏆 Top 10 Oldest Server Members (by join date)**")
        if not joined_members:
            await ctx.send("No members with join dates found in the server.")
        else:
            for i, member in enumerate(joined_members, 1):
                embed = await create_member_embed(member, i, discord.Color.gold())
                await ctx.send(embed=embed)

        await ctx.send("**🕰️ Top 10 Oldest Discord Accounts (by creation date)**")
        for i, member in enumerate(created_members, 1):
            embed = await create_member_embed(member, i, discord.Color.blue())
            await ctx.send(embed=embed)

    @handle_errors
    async def show_info(self, ctx) -> None:
        """(Command) Sends the pre-configured info messages to the channel."""
        command_name = f"!{ctx.invoked_with}"
        record_command_usage(self.state.analytics, command_name)
        record_command_usage_by_user(self.state.analytics, ctx.author.id, command_name)
        for msg in self.bot_config.INFO_MESSAGES: await ctx.send(msg)

    @handle_errors
    async def list_roles(self, ctx) -> None:
        """(Command) Lists all roles in the server and the members in each role."""
        record_command_usage(self.state.analytics, "!roles")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!roles")

        for role in sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True):
            if role.name != "@everyone" and role.members:
                # Sort members alphabetically by their real username (case-insensitive)
                sorted_members = sorted(role.members, key=lambda m: m.name.lower())

                def process_member(member): return f"{member.display_name} ({member.name}#{member.discriminator})"

                embeds = create_message_chunks(
                    entries=sorted_members, title=f"Role: {role.name}", process_entry=process_member,
                    as_embed=True, embed_color=role.color or discord.Color.default()
                )
                for i, embed in enumerate(embeds):
                    if len(embeds) > 1: embed.title = f"{embed.title} (Part {i + 1})"
                    embed.set_footer(text=f"Total members: {len(role.members)}")
                    await ctx.send(embed=embed)

    @handle_errors
    async def show_admin_list(self, ctx) -> None:
        """(Command) Lists all configured bot owners and server admins."""
        from tools import build_embed
        record_command_usage(self.state.analytics, "!admin")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!admin")
        guild = ctx.guild
        if not guild: return

        owners_list = []
        for user_id in self.bot_config.ALLOWED_USERS:
            member = guild.get_member(user_id)
            if member: owners_list.append(f"{member.name} ({member.display_name})")
            else:
                try:
                    user = await self.bot.fetch_user(user_id)
                    owners_list.append(f"{user.name} (Not in server, ID: {user_id})")
                except discord.NotFound: owners_list.append(f"Unknown User (ID: {user_id})")

        admins_set = set()
        admin_roles = [role for role in guild.roles if role.name in self.bot_config.ADMIN_ROLE_NAME]
        for role in admin_roles:
            for member in role.members:
                if member.id not in self.bot_config.ALLOWED_USERS: admins_set.add(f"{member.name} ({member.display_name})")

        owners_text = "\n".join(sorted(owners_list)) if owners_list else "👑 No owners found."
        admins_text = "\n".join(sorted(list(admins_set))) if admins_set else "🛡️ No admins found."

        embed_owners = build_embed("👑 Owners", owners_text, discord.Color.gold())
        embed_admins = build_embed("🛡️ Admins", admins_text, discord.Color.red())

        await ctx.send(embed=embed_owners)
        await ctx.send(embed=embed_admins)

    @handle_errors
    async def show_commands_list(self, ctx) -> None:
        """(Command) Displays a formatted list of all available bot commands, sorted by permission level."""
        from tools import build_embed
        record_command_usage(self.state.analytics, "!commands")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!commands")

        user_commands = (
            "`!skip` - Skips the current Omegle user.\n"
            "`!refresh` - Refreshes the Omegle page.\n"
            "`!info` - Shows server info/rules.\n"
            "`!rules` - Shows the server rules.\n"
            "`!times` - Shows top VC users by time.\n"
            "`!mskip` - Skips the current song.\n"
            "`!mpp` - Toggles music play and pause.\n"
            "`!vol 1-100` - Sets music volume (0-100).\n"
            "`!m songname` - Searches for songs/URLs.\n"
            "`!mclear` - Clears all songs from the search queue.\n"
            "`!mshuffle` - Cycles music mode (Shuffle -> Alphabetical -> Loop).\n"
            "`!np` - Shows currently playing song.\n"
            "`!q` - Displays the interactive song queue.\n"
            "`!playlist <save|load|list|delete> [name]` - Manages playlists."
        )

        admin_commands = (
            "`!report` - Reports the current user on Omegle.\n"
            "`!help` - Sends the interactive help menu with buttons.\n"
            "`!music` - Sends the interactive music control menu.\n"
            "`!moff` - Disables all music features and disconnects the bot.\n"
            "`!mon` - Enables all music features and connects the bot.\n"
            "`!timeouts` - Shows currently timed-out users.\n"
            "`!rtimeouts` - Removes all active timeouts from users.\n"
            "`!display <user>` - Shows a detailed profile for a user.\n"
            "`!commands` - Shows this list of all commands."
        )

        allowed_commands = (
            "`!purge <count>` - Purges a specified number of messages.\n"
            "`!hush` - Server-mutes all non-admin users in the Streaming VC.\n"
            "`!rhush` / `!removehush` - Removes server-mutes from all users.\n"
            "`!secret` - Server-mutes and deafens all non-admin users.\n"
            "`!rsecret` / `!removesecret` - Removes mute/deafen from all users.\n"
            "`!modoff` / `!modon` - Toggles automated VC moderation.\n"
            "`!disablenotifications` / `!enablenotifications` - Toggles event notifications.\n"
            "`!ban <user>` - Bans user(s) with a reason prompt.\n"
            "`!unban <user_id>` - Unbans a user by ID.\n"
            "`!unbanall` - Unbans every user from the server.\n"
            "`!disable <user>` - Disables a user from using commands.\n"
            "`!enable <user>` - Re-enables a disabled user.\n"
            "`!top` - Lists the top 10 oldest server/Discord accounts.\n"
            "`!roles` - Lists all server roles and their members.\n"
            "`!admin` / `!owner` - Lists configured bot owners and admins.\n"
            "`!whois` - Shows a 24-hour report of server activity.\n"
            "`!stats` - Shows a detailed analytics report.\n"
            "`!join` - DMs a join invite to all users with an admin role.\n"
            "`!clearstats` - Clears all statistical data.\n"
            "`!clearwhois` - Clears all historical event data.\n"
            "`!shutdown` - Safely shuts down the bot."
        )

        await ctx.send(embed=build_embed("👤 User Commands (Camera On)", user_commands, discord.Color.blue()))
        await ctx.send(embed=build_embed("🛡️ Admin Commands (Camera On)", admin_commands, discord.Color.red()))
        await ctx.send(embed=build_embed("👑 Owner Commands (No Requirements)", allowed_commands, discord.Color.gold()))

    @handle_errors
    async def show_whois(self, ctx) -> None:
        """(Command) Displays a comprehensive report of recent moderation and member activities."""
        record_command_usage(self.state.analytics, "!whois")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!whois")

        now = datetime.now(timezone.utc)
        reports = {}
        has_data = False

        # --- Data Gathering ---
        async with self.state.moderation_lock:
            time_filter = now - timedelta(hours=24)
            timed_out_members = [member for member in ctx.guild.members if member.is_timed_out()]
            untimeout_list = [e for e in self.state.recent_untimeouts if e[3] >= time_filter and (len(e) > 5 and e[5] and e[5] != "System")]
            kick_list = [e for e in self.state.recent_kicks if e[3] >= time_filter]
            ban_list = [e for e in self.state.recent_bans if e[3] >= time_filter]
            unban_list = [e for e in self.state.recent_unbans if e[3] >= time_filter]
            join_list = [e for e in self.state.recent_joins if e[3] >= time_filter]
            leave_list = [e for e in self.state.recent_leaves if e[3] >= time_filter]
            role_change_list = [e for e in self.state.recent_role_changes if e[4] >= time_filter]


        # Inside show_whois()

        # --- User Info Mapping ---
        user_ids_to_map = {entry[0] for data_list in [untimeout_list, kick_list, ban_list, unban_list, join_list, leave_list, role_change_list] for entry in data_list}
        user_map = {}
        if user_ids_to_map:
            # First, efficiently populate the map with users already in the server cache.
            ids_to_fetch = set()
            for user_id in user_ids_to_map:
                if member := ctx.guild.get_member(user_id):
                    user_map[user_id] = member
                else:
                    # Collect all IDs that need to be fetched via the API.
                    ids_to_fetch.add(user_id)

            # Concurrently fetch all users who were not in the cache.
            if ids_to_fetch:
                # This helper coroutine fetches a user and handles potential errors.
                async def fetch_user(uid):
                    await asyncio.sleep(0.1) # Add a small delay to space out API calls
                    try:
                        return uid, await self.bot.fetch_user(uid)
                    except discord.NotFound:
                        return uid, None # User doesn't exist.
                    except Exception as e:
                        logger.warning(f"Could not fetch user {uid} for whois report: {e}")
                        return uid, None # Other potential error.

                # Create a list of tasks to run in parallel.
                fetch_tasks = [fetch_user(uid) for uid in ids_to_fetch]
                # asyncio.gather runs all fetch tasks at once and waits for them to complete.
                results = await asyncio.gather(*fetch_tasks)

                # Populate the user_map with the results from the concurrent fetches.
                for uid, user_obj in results:
                    user_map[uid] = user_obj

        # --- Helper Functions ---
        def get_clean_mention(identifier):
            if identifier is None:
                return "Unknown"

            # This is the preferred method as IDs are unique and always work.
            if isinstance(identifier, int):
                # We can create a valid mention using their ID, which is 100% accurate
                # even if the user has left the server.
                return f"<@{identifier}>"

            # If the identifier is not an int (e.g., a string like "System" or "AutoMod"),
            # just return it as is.
            return str(identifier)

        def get_user_display_info(user_id, stored_username=None, stored_display_name=None):
            user = user_map.get(user_id)
            if user: return f"{user.mention} ({user.name})"
            name = stored_username or "Unknown User"
            return f"`{name}` <@{user_id}>"

        # --- Report Generation ---
        if timed_out_members:
            has_data = True
            def process_timeout(member):
                data = self.state.active_timeouts.get(member.id, {})
                timed_by = data.get("timed_by_id", data.get("timed_by", "Unknown"))
                reason = data.get("reason", "No reason provided")
                start_ts = data.get("start_timestamp")

                line = f"• {member.mention} - by {get_clean_mention(timed_by)}"
                if reason and reason != "No reason provided":
                    line += f" for *{reason}*"

                if start_ts:
                    line += f" | <t:{int(start_ts)}:R>"
                return line
            reports["⏳ Timed Out Members"] = create_message_chunks(timed_out_members, "⏳ Timed Out Members", process_timeout, as_embed=False)

        if untimeout_list:
            has_data = True
            def process_untimeout(entry):
                uid, _, _, ts, _, mod_name, mod_id = entry
                mod_mention = get_clean_mention(mod_id or mod_name)
                return f"• <@{uid}> - by {mod_mention} <t:{int(ts.timestamp())}:R>"
            reports["🔓 Recent Untimeouts"] = create_message_chunks(untimeout_list, "🔓 Recent Untimeouts (24h)", process_untimeout, as_embed=True, embed_color=discord.Color.from_rgb(173, 216, 230))

        if kick_list:
            has_data = True
            def process_kick(entry):
                uid, name, dname, ts, reason, mod, _ = entry
                user_info = get_user_display_info(uid, name, dname)
                line = f"• {user_info} - by {mod}"
                if reason and reason != "No reason provided":
                    line += f" for *{reason}*"
                line += f" <t:{int(ts.timestamp())}:R>"
                return line
            reports["👢 Recent Kicks"] = create_message_chunks(kick_list, "👢 Recent Kicks (24h)", process_kick, as_embed=True, embed_color=discord.Color.orange())

        if ban_list:
            has_data = True
            def process_ban(entry):
                uid, name, dname, ts, reason = entry
                user_info = get_user_display_info(uid, name, dname)
                line = f"• {user_info}"
                if reason and reason != "No reason provided":
                    line += f" - for *{reason}*"
                line += f" <t:{int(ts.timestamp())}:R>"
                return line
            reports["🔨 Recent Bans"] = create_message_chunks(ban_list, "🔨 Recent Bans (24h)", process_ban, as_embed=True, embed_color=discord.Color.dark_red())

        if unban_list:
            has_data = True
            def process_unban(entry):
                uid, name, dname, ts, mod = entry
                user_info = get_user_display_info(uid, name, dname)
                return f"• {user_info} - by {mod} <t:{int(ts.timestamp())}:R>"
            reports["🔓 Recent Unbans"] = create_message_chunks(unban_list, "🔓 Recent Unbans (24h)", process_unban, as_embed=True, embed_color=discord.Color.dark_green())

        if role_change_list:
            has_data = True
            def process_role_change(entry):
                uid, name, gained, lost, ts = entry
                user_info = get_user_display_info(uid, name)
                parts = [f"• {user_info} <t:{int(ts.timestamp())}:R>"]
                if gained: parts.append(f"  - **Gained**: {', '.join(gained)}")
                if lost: parts.append(f"  - **Lost**: {', '.join(lost)}")
                return parts
            reports["🎭 Recent Role Changes"] = create_message_chunks(role_change_list, "🎭 Recent Role Changes (24h)", process_role_change, as_embed=True, embed_color=discord.Color.purple())

        if join_list:
            has_data = True
            def process_join(entry):
                uid, name, dname, ts = entry
                user_info = get_user_display_info(uid, name, dname)
                return f"• {user_info} <t:{int(ts.timestamp())}:R>"
            reports["🎉 Recent Joins"] = create_message_chunks(join_list, "🎉 Recent Joins (24h)", process_join, as_embed=True, embed_color=discord.Color.green())

        if leave_list:
            has_data = True
            def process_leave(entry):
                uid, name, dname, ts, _ = entry
                user_info = get_user_display_info(uid, name, dname)
                return f"• {user_info} <t:{int(ts.timestamp())}:R>"
            reports["🚪 Recent Leaves"] = create_message_chunks(leave_list, "🚪 Recent Leaves (24h)", process_leave, as_embed=True, embed_color=discord.Color.red())

        # --- Displaying Reports ---
        if not has_data:
            await ctx.send("📭 No recent activity found in the last 24 hours.")
            return

        report_order = ["⏳ Timed Out Members", "🔓 Recent Untimeouts", "👢 Recent Kicks", "🔨 Recent Bans", "🔓 Recent Unbans", "🎭 Recent Role Changes", "🎉 Recent Joins", "🚪 Recent Leaves"]
        for report_type in report_order:
            if report_type in reports:
                for chunk in reports[report_type]:
                    if isinstance(chunk, discord.Embed):
                        await ctx.send(embed=chunk)
                    else:
                        await ctx.send(chunk)
                    await asyncio.sleep(0.5)

    @handle_errors
    async def remove_timeouts(self, ctx) -> None:
        """(Command) Removes all active timeouts from members in the server."""
        record_command_usage(self.state.analytics, "!rtimeouts")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!rtimeouts")

        timed_out_members = [m for m in ctx.guild.members if m.is_timed_out()]
        if not timed_out_members:
            await ctx.send("No users are currently timed out.")
            return

        confirm_msg = await ctx.send(f"⚠️ **WARNING:** This will remove timeouts from {len(timed_out_members)} members!\nReact with ✅ to confirm or ❌ to cancel within 30 seconds.")
        for emoji in ["✅", "❌"]: await confirm_msg.add_reaction(emoji)

        def check(reaction, user): return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id
        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            if str(reaction.emoji) == "❌": await ctx.send("Command cancelled."); return
        except asyncio.TimeoutError:
            await ctx.send("⌛ Command timed out. No changes were made."); return

        removed, failed = [], []
        for member in timed_out_members:
            try:
                await member.timeout(None, reason=f"Timeout removed by {ctx.author.name} ({ctx.author.id})")
                removed.append(member.name)
                async with self.state.moderation_lock:
                    if member.id in self.state.active_timeouts:
                        self.state.recent_untimeouts.append((member.id, member.name, member.display_name, datetime.now(timezone.utc), f"Manually removed by {ctx.author.name}", ctx.author.name, ctx.author.id))
                        del self.state.active_timeouts[member.id]
                logger.info(f"Removed timeout from {member.name} by {ctx.author.name}")
            except discord.Forbidden: failed.append(f"{member.name} (Missing Permissions)")
            except discord.HTTPException as e: failed.append(f"{member.name} (Error: {e})")

        result_msg = []
        if removed: result_msg.append(f"**✅ Removed timeouts from:**\n- " + "\n".join(removed))
        if failed: result_msg.append(f"\n**❌ Failed to remove timeouts from:**\n- " + "\n".join(failed))
        if result_msg: await ctx.send("\n".join(result_msg))

        if chat_channel := ctx.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID):
            await chat_channel.send(f"⏰ **Mass Timeout Removal**\nExecuted by {ctx.author.mention}\nRemoved: {len(removed)} | Failed: {len(failed)}")

    @handle_errors
    async def show_rules(self, ctx) -> None:
        """(Command) Posts the server rules to the channel."""
        record_command_usage(self.state.analytics, "!rules")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!rules")
        await ctx.send("📋 **Server Rules:**\n" + self.bot_config.RULES_MESSAGE)

    @handle_errors
    async def show_timeouts(self, ctx) -> None:
        """(Command) Displays a report of current timeouts, untimeouts, and disabled users."""
        record_command_usage(self.state.analytics, "!timeouts")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!timeouts")

        reports, has_data = {}, False

        def get_clean_mention(identifier):
            if identifier is None:
                return "Unknown"

            # This is the preferred method as IDs are unique and always work.
            if isinstance(identifier, int):
                # We can create a valid mention using their ID, which is 100% accurate
                # even if the user has left the server.
                return f"<@{identifier}>"

            # If the identifier is not an int (e.g., a string like "System" or "AutoMod"),
            # just return it as is.
            return str(identifier)

        # --- 1. Currently Timed Out Users ---
        timed_out_members = [member for member in ctx.guild.members if member.is_timed_out()]
        if timed_out_members:
            has_data = True

            # First, gather all necessary data in one async block to be efficient.
            timeout_data = {}
            async with self.state.moderation_lock:
                for member in timed_out_members:
                    timeout_data[member.id] = self.state.active_timeouts.get(member.id, {})

            # Then, process the collected data synchronously.
            def process_timeout(member):
                data = timeout_data.get(member.id, {})
                timed_by = data.get("timed_by_id", data.get("timed_by"))
                reason = data.get("reason")
                start_ts = data.get("start_timestamp")

                line = f"• {member.mention}"
                if timed_by and timed_by != "Unknown":
                    line += f" - by {get_clean_mention(timed_by)}"
                if reason and reason != "No reason provided":
                    line += f" for *{reason}*"

                if start_ts:
                    line += f" | <t:{int(start_ts)}:R>"
                return line

            processed_timeouts = [process_timeout(m) for m in timed_out_members]
            reports["⏳ Currently Timed Out"] = create_message_chunks(processed_timeouts, "⏳ Currently Timed Out", lambda x: x, as_embed=False)

        # --- 2. History of Manual Untimeouts ---
        async with self.state.moderation_lock:
            untimeout_entries = [e for e in self.state.recent_untimeouts if len(e) > 5 and e[5] and e[5] != "System"]
        if untimeout_entries:
            has_data = True
            processed_users = set()

            def process_untimeout(entry):
                user_id = entry[0]
                ts = entry[3]
                mod_name = entry[5]
                mod_id = entry[6] if len(entry) > 6 else None
                mod_mention = get_clean_mention(mod_id) if mod_id else get_clean_mention(mod_name)

                line = f"• <@{user_id}>"
                if mod_mention and mod_mention != "Unknown":
                    line += f" - Removed by: {mod_mention}"

                line += f" <t:{int(ts.timestamp())}:R>"
                return line

            unique_untimeout_entries = []
            for entry in reversed(untimeout_entries):
                if entry[0] not in processed_users:
                    unique_untimeout_entries.append(entry)
                    processed_users.add(entry[0])

            processed_untimeouts = [process_untimeout(e) for e in reversed(unique_untimeout_entries)]
            reports["🔓 All Untimeouts"] = create_message_chunks(processed_untimeouts, "🔓 All Untimeouts", lambda x: x, as_embed=True, embed_color=discord.Color.blue())

        # --- 3. Command Disabled Users ---
        async with self.state.moderation_lock:
            disabled_user_ids = list(self.state.omegle_disabled_users)

        if disabled_user_ids:
            has_data = True
            async def process_disabled_user(user_id):
                try:
                    user = await self.bot.fetch_user(user_id)
                    return f"• {user.mention} (`{user.name}`)"
                except discord.NotFound:
                    return f"• Unknown User (ID: `{user_id}`)"
                except Exception as e:
                    logger.warning(f"Could not fetch user {user_id} for disabled list: {e}")
                    return f"• Error fetching User ID `{user_id}`"

            processed_disabled = await asyncio.gather(*(process_disabled_user(uid) for uid in disabled_user_ids))
            reports["🚫 Command Disabled Users"] = create_message_chunks(
                entries=processed_disabled,
                title="🚫 Command Disabled Users",
                process_entry=lambda x: x,
                as_embed=True,
                embed_color=discord.Color.dark_grey()
            )

        # --- Display Reports ---
        report_order = ["🚫 Command Disabled Users", "⏳ Currently Timed Out", "🔓 All Untimeouts"]
        for report_type in report_order:
            if report_type in reports and reports[report_type]:
                for chunk in reports[report_type]:
                    if isinstance(chunk, discord.Embed):
                        await ctx.send(embed=chunk)
                    else:
                        await ctx.send(chunk)

        if not has_data:
            await ctx.send("📭 No active timeouts, untimeouts, or disabled users found.")


    async def create_times_report_embed(self) -> Optional[discord.Embed]:
        """
        An internal helper function to generate the complete voice channel time report embed.
        """
        guild = self.bot.get_guild(self.bot_config.GUILD_ID)
        if not guild:
            return None

        async def get_user_display_info(user_id, data):
            """Gets a user's display info, trying the live member object first then falling back to stored data."""
            if member := guild.get_member(user_id):
                roles = [role for role in member.roles if role.name != "@everyone"]
                highest_role = max(roles, key=lambda r: r.position) if roles else None
                role_display = f"**[{highest_role.name}]**" if highest_role else ""
                return f"{member.mention} {role_display}"
            username = data.get("username", "Unknown User")
            return f"`{username}` (Left/Not Found)"

        def is_excluded(user_id): return user_id in self.bot_config.STATS_EXCLUDED_USERS

        async def get_vc_time_data():
            """Calculates total VC time for all users, including current active sessions."""
            async with self.state.vc_lock:
                current_time = time.time()
                combined_data = {uid: d.copy() for uid, d in self.state.vc_time_data.items() if not is_excluded(uid)}
                total_time_all_users = sum(d.get("total_time", 0) for d in combined_data.values())

                for user_id, start_time in self.state.active_vc_sessions.items():
                    if is_excluded(user_id): continue
                    active_duration = current_time - start_time
                    if user_id in combined_data: combined_data[user_id]["total_time"] += active_duration
                    else:
                        member = guild.get_member(user_id)
                        combined_data[user_id] = { "total_time": active_duration, "username": member.name if member else "Unknown", "display_name": member.display_name if member else "Unknown" }
                    total_time_all_users += active_duration

            sorted_users = sorted(combined_data.items(), key=lambda item: item[1].get("total_time", 0), reverse=True)[:10]
            return total_time_all_users, sorted_users

        # Calculate statistics
        total_tracking_seconds = 0
        async with self.state.vc_lock:
            all_start_times = [s["start"] for d in self.state.vc_time_data.values() for s in d.get("sessions", []) if "start" in s]
            all_start_times.extend(self.state.active_vc_sessions.values())
            if all_start_times:
                total_tracking_seconds = time.time() - min(all_start_times)

        total_time_all_users, top_vc_users = await get_vc_time_data()

        average_user_count = 0
        if total_tracking_seconds > 60:
            average_user_count = round(total_time_all_users / total_tracking_seconds)

        # Build the embed description
        description_lines = []
        tracking_time_str = format_duration(total_tracking_seconds)
        description_lines.append(f"⏳ **Tracking Started:** {tracking_time_str} ago")
        if average_user_count > 0:
            description_lines.append(f"👥 **Average User Count:** {average_user_count}")

        description_lines.append("")

        if top_vc_users:
            for i, (uid, data) in enumerate(top_vc_users):
                total_s = data.get('total_time', 0)
                time_str = format_duration(total_s)
                display_info = await get_user_display_info(uid, data)
                description_lines.append(f"**{i+1}.** {display_info}: **{time_str}**")
        else:
            description_lines.append("No VC time data available yet.")

        total_hours = math.ceil(total_time_all_users / 3600)
        total_time_str = f"{total_hours} hours"
        description_lines.append("")
        description_lines.append(f"⏱ **Total VC Time (All Users):** {total_time_str}")

        embed = discord.Embed(
            title="🏆 Top 10 VC Members",
            description="\n".join(description_lines),
            color=discord.Color.gold()
        )
        return embed

    @handle_errors
    async def show_times_report(self, destination: Union[commands.Context, discord.TextChannel]) -> Optional[discord.Message]:
        """
        (Command) Public-facing function to show the VC time report.
        Now returns the message object.
        """
        channel = destination.channel if isinstance(destination, commands.Context) else destination
        if isinstance(destination, commands.Context):
            record_command_usage(self.state.analytics, "!times")
            record_command_usage_by_user(self.state.analytics, destination.author.id, "!times")

        embed = await self.create_times_report_embed()
        if embed:
            return await channel.send(embed=embed)
        return None

    @handle_errors
    async def show_analytics_report(self, destination: Union[commands.Context, discord.TextChannel]) -> None:
        """(Command) Shows a detailed report of VC time, command usage, and moderation events."""
        if isinstance(destination, commands.Context):
            ctx = destination
            channel = ctx.channel
            record_command_usage(self.state.analytics, "!stats")
            record_command_usage_by_user(self.state.analytics, ctx.author.id, "!stats")
        else:
            ctx = None # No context when run from a task
            channel = destination

        guild = channel.guild

        # MODIFIED: Call the new times report function
        await self.show_times_report(channel)
        await channel.send("\n" + "─"*50 + "\n")

        async def get_user_display_info(user_id):
            """Helper to get a rich display name for a user in the stats report."""
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                if member := guild.get_member(user_id):
                    roles = [role for role in member.roles if role.name != "@everyone"]
                    highest_role = max(roles, key=lambda r: r.position) if roles else None
                    role_display = f"**[{highest_role.name}]**" if highest_role else ""
                    return f"{member.mention} {role_display} ({member.name})"
                return f"{user.mention} ({user.name})"
            except Exception: return f"<@{user_id}> (Unknown User)"

        async def get_user_plain_name(user_id):
            """Helper to get a plain username without mentions for the stats report."""
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                return f"`{user.name}#{user.discriminator}`"
            except Exception:
                # Fallback for users who have left the server, using stored data if available
                async with self.state.vc_lock:
                    vc_data = self.state.vc_time_data.get(user_id, {})
                    username = vc_data.get("username", f"ID: {user_id}")
                return f"`{username}` (Left/Not Found)"


        def is_excluded(user_id): return user_id in self.bot_config.STATS_EXCLUDED_USERS

        has_any_stats_data = False

        # --- FIX: Each stats block is now self-contained ---

        # Overall Command Usage
        async with self.state.analytics_lock:
            command_usage_data = self.state.analytics.get("command_usage")

        if command_usage_data:
            has_any_stats_data = True
            sorted_commands = sorted(command_usage_data.items(), key=lambda x: x[1], reverse=True)
            for chunk in create_message_chunks(sorted_commands, "📊 Overall Command Usage", lambda cmd: f"• `{cmd[0]}`: {cmd[1]} times", as_embed=True, embed_color=discord.Color.blue()):
                await channel.send(embed=chunk)

        # Top Command Users
        async with self.state.analytics_lock:
            usage_by_user_data = self.state.analytics.get("command_usage_by_user")

        if usage_by_user_data:
            has_any_stats_data = True
            filtered_users = [(uid, cmds) for uid, cmds in usage_by_user_data.items() if not is_excluded(uid)]
            sorted_users = sorted(filtered_users, key=lambda item: sum(item[1].values()), reverse=True)[:10]

            async def process_user_usage(entry):
                uid, cmds = entry
                usage = ", ".join([f"{c}: {cnt}" for c, cnt in sorted(cmds.items(), key=lambda x: x[1], reverse=True)])
                # Use the new helper function to get a plain username
                user_display = await get_user_plain_name(uid)
                return f"• {user_display}: {usage}"

            if sorted_users:
                processed_entries = await asyncio.gather(*(process_user_usage(entry) for entry in sorted_users))
                for chunk in create_message_chunks(processed_entries, "👤 Top 10 Command Users", lambda x: x, as_embed=True, embed_color=discord.Color.green()):
                    await channel.send(embed=chunk)

        # No-Cam Violation Report
        async with self.state.moderation_lock:
            user_violations_data = self.state.user_violations

        if user_violations_data:
            has_any_stats_data = True
            filtered_violations = [(uid, count) for uid, count in user_violations_data.items() if not is_excluded(uid)]
            sorted_violations = sorted(filtered_violations, key=lambda item: item[1], reverse=True)[:10]

            async def process_violation(entry):
                uid, count = entry
                if member := guild.get_member(uid):
                    user_display_str = f"`{member.name}` (`{member.display_name}`)" if member.name != member.display_name else f"`{member.name}`"
                else:
                    try: user_display_str = f"`{(await self.bot.fetch_user(uid)).name}` (Left Server)"
                    except discord.NotFound: user_display_str = f"Unknown User (ID: `{uid}`)"
                return f"• {user_display_str}: {count} violation(s)"

            if sorted_violations:
                processed_entries = await asyncio.gather(*(process_violation(entry) for entry in sorted_violations))
                for chunk in create_message_chunks(processed_entries, "⚠️ No-Cam Detected Report", lambda x: x, as_embed=True, embed_color=discord.Color.orange()):
                    await channel.send(embed=chunk)

        # Send a message if no data was found at all
        if not has_any_stats_data:
            await channel.send("📊 No command/violation statistics available yet.")

    @handle_errors
    async def send_join_invites(self, ctx) -> None:
        """(Command) Sends a pre-configured DM to all users with an admin role."""
        record_command_usage(self.state.analytics, "!join")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!join")

        guild = ctx.guild
        admin_role_names = self.bot_config.ADMIN_ROLE_NAME
        join_message = self.bot_config.JOIN_INVITE_MESSAGE

        admin_roles = [role for role in guild.roles if role.name in admin_role_names]
        if not admin_roles:
            await ctx.send("No admin roles found with the specified names."); return

        members_to_dm = {member for role in admin_roles for member in role.members}
        if not members_to_dm:
            await ctx.send("No members with the specified admin roles found to DM."); return

        await ctx.send(f"Sending invites to {len(members_to_dm)} member(s) with the role(s): {', '.join(admin_role_names)}. This may take a moment...")

        impacted = []
        for member in members_to_dm:
            if member.bot: continue
            try:
                await member.send(join_message)
                impacted.append(member.name)
                logger.info(f"Sent join invite to {member.name}.")
                await asyncio.sleep(1)
            except discord.Forbidden: logger.warning(f"Could not DM {member.name} (DMs are disabled or bot is blocked).")
            except Exception as e: logger.error(f"Error DMing {member.name}: {e}")

        if impacted:
            msg = "Finished sending invites. Sent to: " + ", ".join(impacted)
            logger.info(msg)
            await ctx.send(msg)
        else: await ctx.send("Finished processing. No invites were successfully sent.")

    @handle_errors
    async def clear_whois_data(self, ctx) -> None:
        """(Command) Clears all historical data used by the !whois command."""
        confirm_msg = await ctx.send("⚠️ This will reset ALL historical event data for `!whois` (joins, leaves, bans, etc.). This cannot be undone.\nReact with ✅ to confirm or ❌ to cancel.")
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user): return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id
        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            if str(reaction.emoji) == "✅":
                async with self.state.moderation_lock:
                    self.state.recent_joins.clear()
                    self.state.recent_leaves.clear()
                    self.state.recent_bans.clear()
                    self.state.recent_kicks.clear()
                    self.state.recent_unbans.clear()
                    self.state.recent_untimeouts.clear()
                    self.state.recent_role_changes.clear()

                await ctx.send("✅ All `!whois` historical data has been reset.")
                logger.info(f"`!whois` data cleared by {ctx.author.name} (ID: {ctx.author.id})")
                if self.save_state:
                    await self.save_state()
            else:
                await ctx.send("❌ Whois data reset cancelled.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Command timed out. No changes were made.")
        finally:
            try: await confirm_msg.delete()
            except Exception: pass

    @handle_errors
    async def clear_stats(self, ctx) -> None:
        """(Command) Resets all statistical data after a confirmation prompt."""
        confirm_msg = await ctx.send("⚠️ This will reset ALL statistics data (VC times, command usage, violations).\nReact with ✅ to confirm or ❌ to cancel.")
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user): return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id
        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            if str(reaction.emoji) == "✅":
                guild = ctx.guild
                streaming_vc = guild.get_channel(self.bot_config.STREAMING_VC_ID)
                alt_vc = guild.get_channel(self.bot_config.ALT_VC_ID) if self.bot_config.ALT_VC_ID else None
                current_members = []
                if streaming_vc: current_members.extend([m for m in streaming_vc.members if not m.bot])
                if alt_vc: current_members.extend([m for m in alt_vc.members if not m.bot])

                async with self.state.vc_lock, self.state.analytics_lock, self.state.moderation_lock:
                    self.state.vc_time_data = {}
                    self.state.active_vc_sessions = {}
                    self.state.analytics = {"command_usage": {}, "command_usage_by_user": {}, "violation_events": 0}
                    self.state.user_violations = {}
                    self.state.camera_off_timers = {}

                    if current_members:
                        current_time = time.time()
                        for member in current_members:
                            self.state.active_vc_sessions[member.id] = current_time
                            self.state.vc_time_data[member.id] = {"total_time": 0, "sessions": [], "username": member.name, "display_name": member.display_name}
                        logger.info(f"Restarted VC tracking for {len(current_members)} current members")
                await ctx.send("✅ All statistics data has been reset.")
                logger.info(f"Statistics cleared by {ctx.author.name} (ID: {ctx.author.id})")
                if self.save_state:
                    await self.save_state()
            else: await ctx.send("❌ Statistics reset cancelled.")
        except asyncio.TimeoutError: await ctx.send("⌛ Command timed out. No changes were made.")
        finally:
            try: await confirm_msg.delete()
            except Exception: pass

    @handle_errors
    async def show_user_display(self, ctx, member: discord.Member) -> None:
        """(Command) Displays a rich embed of a user's profile."""
        record_command_usage(self.state.analytics, "!display")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!display")

        user_obj = member
        try:
            fetched_user = await self.bot.fetch_user(member.id)
            if fetched_user:
                user_obj = fetched_user
        except Exception:
            pass

        embed = discord.Embed(description=f"{member.mention}", color=discord.Color.blue())

        # It checks if the discriminator is '0' and formats the name accordingly.
        author_name = member.name if member.discriminator == '0' else f"{member.name}#{member.discriminator}"
        embed.set_author(name=author_name, icon_url=member.display_avatar.url)

        embed.set_thumbnail(url=member.display_avatar.url)
        if hasattr(user_obj, 'banner') and user_obj.banner:
            embed.set_image(url=user_obj.banner.url)

        embed.add_field(name="Account Created", value=f"{member.created_at.strftime('%m-%d-%Y')}\n({get_discord_age(member.created_at)} old)", inline=True)
        if member.joined_at:
            embed.add_field(name="Joined Server", value=f"{member.joined_at.strftime('%m-%d-%Y')}\n({get_discord_age(member.joined_at)} ago)", inline=True)

        embed.add_field(name="User ID", value=str(member.id), inline=False)

        roles = [role.mention for role in member.roles if role.name != "@everyone"]
        if roles:
            roles.reverse()
            role_str = " ".join(roles)
            if len(role_str) > 1024:
                role_str = "Too many roles to display."
            embed.add_field(name=f"Roles", value=role_str, inline=False)

        await ctx.send(embed=embed)

    # --- NEW MUSIC HELPER METHODS ---

    async def create_music_menu_embed_and_view(self) -> Tuple[Optional[discord.Embed], Optional[View]]:
        """
        Creates the music menu embed and view based on the current state.
        This is separated to allow both sending and editing the menu.
        """
        if not self.state.music_enabled:
            return None, None

        try:
            status_lines = []
            async with self.state.music_lock:
                if self.state.is_music_playing and self.state.current_song:
                    status_lines.append(f"**Now Playing:** `{self.state.current_song['title']}`")
                elif self.state.is_music_paused and self.state.current_song:
                     status_lines.append(f"**Paused:** `{self.state.current_song['title']}`")
                else:
                    status_lines.append("**Now Playing:** Nothing")

                current_mode_str = self.state.music_mode.capitalize()
                status_lines.append(f"**Mode:** {current_mode_str}")

                # Calculate display volume as a percentage of the configured max volume
                display_volume = 0
                if self.bot_config.MUSIC_MAX_VOLUME > 0:
                    display_volume = int((self.state.music_volume / self.bot_config.MUSIC_MAX_VOLUME) * 100)

                status_lines.append(f"**Volume:** {display_volume}%")

                queue = self.state.active_playlist + self.state.search_queue
                if queue:
                    status_lines.append(f"**Queue:** {len(queue)} song(s)")

            description = f"""
**!m song or URL** -------- Find/queue a song
**!q** ---------------------- View the queue
**!np** --------------------- Show current song
**!mclear** ---------------- Clear the Playlist
**!playlist <save/load>** -- Manage playlists

*{" | ".join(status_lines)}*
"""
            embed = discord.Embed(title="🎵  Music Controls 🎵", description=description, color=discord.Color.purple())
            view = MusicView(self.bot_config, self.state)
            return embed, view

        except Exception as e:
            logger.error(f"Error in create_music_menu_embed_and_view: {e}", exc_info=True)
            return None, None

    async def send_music_menu(self, target: Any) -> Optional[discord.Message]:
        """Sends the interactive music control menu and returns the message object."""
        try:
            embed, view = await self.create_music_menu_embed_and_view()
            if not embed or not view:
                logger.warning("Failed to create music menu embed/view.")
                return None

            destination = target.channel if hasattr(target, 'channel') else target

            if destination and hasattr(destination, 'send'):
                message = await destination.send(embed=embed, view=view)
                return message
            else:
                logger.warning(f"Unsupported target type for music menu: {type(target)}")
                return None
        except Exception as e:
            logger.error(f"Error in send_music_menu: {e}", exc_info=True)
            return None

    @handle_errors
    async def confirm_and_clear_music_queue(self, ctx) -> None:
        """(Command) Confirms with the user and clears all music queues and stops playback."""
        record_command_usage(self.state.analytics, "!mclear")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!mclear")

        async with self.state.music_lock:
            # Check all queues and current playback status
            full_queue = self.state.active_playlist + self.state.search_queue
            is_playing = self.bot.voice_client_music and (self.bot.voice_client_music.is_playing() or self.bot.voice_client_music.is_paused())

            if not full_queue and not is_playing:
                await ctx.send("The music queue is already empty and nothing is playing.", delete_after=10)
                return

            queue_length = len(full_queue)

        confirm_msg = await ctx.send(
            f"Are you sure you want to clear the **{queue_length}** songs in the queue and stop playback?\n"
            "React with ✅ to confirm or ❌ to cancel."
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)

            if str(reaction.emoji) == "✅":
                was_playing = False
                async with self.state.music_lock:
                    # Clear all song queues
                    self.state.search_queue.clear()
                    self.state.active_playlist.clear()

                    # Check if music is playing/paused and stop it.
                    if self.bot.voice_client_music and (self.bot.voice_client_music.is_playing() or self.bot.voice_client_music.is_paused()):
                        was_playing = True
                        # Set a flag to tell the 'after' callback (play_next_song) to halt execution.
                        self.state.stop_after_clear = True
                        self.bot.voice_client_music.stop()

                response_text = f"✅ Cleared **{queue_length}** songs from the queue."
                if was_playing:
                    response_text += " and stopped playback."

                await confirm_msg.edit(content=response_text, view=None)
                logger.info(f"Music queue and playback cleared by {ctx.author.name}")
            else:
                await confirm_msg.edit(content="❌ Queue clear cancelled.", view=None)

        except asyncio.TimeoutError:
            await confirm_msg.edit(content="⌛ Queue clear timed out.", view=None)
        finally:
            try:
                await confirm_msg.clear_reactions()
            except discord.HTTPException:
                pass

    @handle_errors
    async def show_now_playing(self, ctx) -> None:
        """(Command) Shows details about the currently playing song."""
        async with self.state.music_lock:
            if not self.state.current_song or not self.bot.voice_client_music or not (self.bot.voice_client_music.is_playing() or self.bot.voice_client_music.is_paused()):
                await ctx.send("Nothing is currently playing.", delete_after=10)
                return

            song_info = self.state.current_song
            title = song_info.get('title', 'Unknown Title')

            embed = discord.Embed(title="🎵", description=f"**{title}**", color=discord.Color.purple())

            if not song_info.get('is_stream', False):
                embed.add_field(name="Source", value="Local Library", inline=True)
            else:
                embed.add_field(name="Source", value="Online Stream", inline=True)

            # Calculate display volume as a percentage of the configured max volume.
            display_volume = 0
            if self.bot_config.MUSIC_MAX_VOLUME > 0:
                display_volume = int((self.state.music_volume / self.bot_config.MUSIC_MAX_VOLUME) * 100)

            embed.add_field(name="Volume", value=f"{display_volume}%", inline=True)
            embed.add_field(name="Mode", value=self.state.music_mode.capitalize(), inline=True)

        await ctx.send(embed=embed)

    @handle_errors
    async def show_queue(self, ctx) -> None:
        """(Command) Displays an interactive list of songs in the queue."""
        async with self.state.music_lock:
            if not self.state.active_playlist and not self.state.search_queue:
                await ctx.send("The music queue is empty.", delete_after=10)
                return

        view = QueueView(self.bot, self.state, ctx.author)
        await view.start()
        view.message = await ctx.send(content=view.get_content(), view=view)
