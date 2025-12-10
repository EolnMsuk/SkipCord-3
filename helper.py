# helper.py

import asyncio
import discord
import math
import os
import time
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional, Union, List, Tuple

from discord.ext import commands
from discord.ui import View, Button
from discord import ButtonStyle, SelectOption
from discord.ui import Select
from loguru import logger

from tools import (
    BotState,
    BotConfig,
    get_discord_age,
    record_command_usage,
    record_command_usage_by_user,
    handle_errors,
    format_duration,
)
from omegle import OmegleHandler


# --- Utility Functions ---

def format_departure_time(duration: timedelta) -> str:
    """
    Formats the duration a user was in the server for leave/kick/ban messages.
    """
    return format_duration(duration)


def create_message_chunks(
    entries: List[Any],
    title: str,
    process_entry: Callable[[Any], str],
    max_chunk_size: int = 50,
    max_length: Optional[int] = None,  # Changed from 4000 to None for dynamic defaulting
    as_embed: bool = False,
    embed_color: Optional[discord.Color] = None,
) -> Union[List[str], List[discord.Embed]]:
    """
    Splits a long list of text entries into multiple messages or embeds
    to avoid Discord's character limits.

    Args:
        entries: The list of items to process.
        title: The title for the message/embed.
        process_entry: A function that converts an entry into a string.
        max_chunk_size: The maximum number of entries per chunk.
        max_length: The maximum character length per chunk. Defaults to 2000 (text) or 4096 (embed).
        as_embed: If True, returns a list of embeds.
        embed_color: Required color if as_embed is True.

    Returns:
        A list of strings or discord.Embeds.
    """
    if as_embed and embed_color is None:
        raise ValueError("embed_color must be provided when as_embed=True")

    # --- FIX: Set safe defaults based on message type ---
    if max_length is None:
        max_length = 4096 if as_embed else 2000

    chunks = []
    current_chunk = []
    current_length = 0
    # Calculate title overhead if not using embeds (embed titles don't count towards description limit)
    title_length = 0 if as_embed else len(f"**{title} ({len(entries)} total)**\n")

    for entry in entries:
        processed_list = process_entry(entry)
        # Allow process_entry to return a list of lines for one entry
        if not isinstance(processed_list, list):
            processed_list = [processed_list]

        for processed in processed_list:
            if processed:
                entry_length = len(processed) + 1  # +1 for the newline
                
                # Check if adding this line exceeds limits (length or item count)
                # We factor in title_length only for the first chunk of a non-embed message if needed,
                # but generally, simpler logic is safer: just check current_length vs max.
                if (
                    current_length + entry_length > max_length and current_chunk
                ) or len(current_chunk) >= max_chunk_size:
                    
                    # Finalize the current chunk
                    if as_embed:
                        embed = discord.Embed(
                            title=title,
                            description="\n".join(current_chunk),
                            color=embed_color,
                        )
                        chunks.append(embed)
                    else:
                        chunks.append(
                            f"**{title} ({len(entries)} total)**\n"
                            + "\n".join(current_chunk)
                        )
                    # Start a new chunk
                    current_chunk = []
                    current_length = 0

                current_chunk.append(processed)
                current_length += entry_length

    # Add the last remaining chunk
    if current_chunk:
        if as_embed:
            embed = discord.Embed(
                title=title,
                description="\n".join(current_chunk),
                color=embed_color,
            )
            chunks.append(embed)
        else:
            chunks.append(
                f"**{title} ({len(entries)} total)**\n"
                + "\n".join(current_chunk)
            )

    return chunks

class VotingBoothView(discord.ui.View):
    """
    Ephemeral view that lets a user cycle through targets.
    """
    def __init__(self, helper, message_id: int, targets: dict, voter_id: int):
        super().__init__(timeout=180) # 3 minutes to vote before closing booth
        self.helper = helper
        self.message_id = message_id
        self.targets = list(targets.items()) # List of (id, name)
        self.voter_id = voter_id
        self.current_index = 0
        
        # Load existing progress
        current_votes = self.helper.state.active_votes[message_id]["votes"]
        # Find the first target this user hasn't voted on yet
        for i, (tid, _) in enumerate(self.targets):
            tid_str = str(tid)
            if str(voter_id) not in current_votes.get(tid_str, {}):
                self.current_index = i
                break
        else:
            # User voted on everyone
            self.current_index = len(self.targets)

        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        if self.current_index < len(self.targets):
            target_id, target_name = self.targets[self.current_index]
            
            # 1. Add SMASH Button First
            smash_btn = discord.ui.Button(label="SMASH", style=discord.ButtonStyle.success, emoji="🔥", custom_id="vote_smash")
            smash_btn.callback = self.smash_callback
            self.add_item(smash_btn)

            # 2. Add PASS Button Second
            pass_btn = discord.ui.Button(label="PASS", style=discord.ButtonStyle.danger, emoji="🚮", custom_id="vote_pass")
            pass_btn.callback = self.pass_callback
            self.add_item(pass_btn)

            # 3. Add Target Name Label Last (Disabled Button)
            self.add_item(discord.ui.Button(
                label=f"Target {self.current_index + 1}/{len(self.targets)}: {target_name}", 
                style=discord.ButtonStyle.secondary, 
                disabled=True
            ))

        else:
            self.add_item(discord.ui.Button(label="✅ All Votes Cast!", style=discord.ButtonStyle.success, disabled=True))

    async def handle_vote(self, interaction: discord.Interaction, choice: str):
        # --- Guard Clause for Race Conditions/Double Clicks ---
        if self.current_index >= len(self.targets):
            # If the user double-clicked the last button, we are already "done".
            # Just refresh the view to show the "All Votes Cast" button and stop.
            self.update_buttons()
            await interaction.response.edit_message(content="**You have already voted on all users.**", view=self)
            return
        # -----------------------------------------------------------

        target_id, _ = self.targets[self.current_index]
        target_id_str = str(target_id)
        voter_id_str = str(self.voter_id)
        
        async with self.helper.state.moderation_lock:
            # Ensure vote structure exists
            if target_id_str not in self.helper.state.active_votes[self.message_id]["votes"]:
                self.helper.state.active_votes[self.message_id]["votes"][target_id_str] = {}
            
            # Record Vote
            self.helper.state.active_votes[self.message_id]["votes"][target_id_str][voter_id_str] = choice
        
        self.current_index += 1
        self.update_buttons()
        
        if self.current_index >= len(self.targets):
            await interaction.response.edit_message(content="**Thanks for voting!** You have voted on all users.", view=self)
             
            # Announce completion in the main channel
            try:
                # Retrieve channel ID from state using the message ID
                vote_data = self.helper.state.active_votes.get(self.message_id)
                if vote_data:
                    channel_id = vote_data["channel_id"]
                    channel = self.helper.bot.get_channel(channel_id)
                    if channel:
                        await channel.send(f"🎉 {interaction.user.mention} has finished voting on the Smash or Pass!")
            except Exception as e:
                logger.error(f"Failed to send vote completion announcement: {e}")
        else:
             await interaction.response.edit_message(view=self)

    async def smash_callback(self, interaction):
        await self.handle_vote(interaction, "smash")

    async def pass_callback(self, interaction):
        await self.handle_vote(interaction, "pass")


class PersistentVoteView(discord.ui.View):
    """
    The main 'Start Voting' button that stays on the message.
    """
    def __init__(self, helper):
        super().__init__(timeout=None) # Persistent
        self.helper = helper

    # ENSURE custom_id IS SET HERE
    @discord.ui.button(label="🗳️ Enter Voting Booth", style=discord.ButtonStyle.primary, custom_id="enter_voting_booth")
    async def enter_booth(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ... your existing logic ...
        message_id = interaction.message.id
        
        if message_id not in self.helper.state.active_votes:
            await interaction.response.send_message("❌ This vote has ended or expired.", ephemeral=True)
            return

        vote_data = self.helper.state.active_votes[message_id]
        targets = vote_data["targets"]
        
        # Check time
        if datetime.now(timezone.utc).timestamp() > vote_data["end_time"]:
            await interaction.response.send_message("🛑 Voting time is up!", ephemeral=True)
            return

        view = VotingBoothView(self.helper, message_id, targets, interaction.user.id)
        
        # --- CHANGED: Removed the vote counting logic and simplified the message ---
        await interaction.response.send_message(
            "Use the buttons below to vote anonymously.", 
            view=view, 
            ephemeral=True
        )

async def _button_callback_handler(
    interaction: discord.Interaction, command: str, helper: "BotHelper"
) -> None:
    """
    Central handler for all button presses from the static menus.
    Updated to defer immediately to prevent interaction timeouts.
    """
    try:
        # --- SOLUTION: Defer Immediately ---
        # We defer ephemerally right away. This gives the bot 15 minutes to process
        # instead of the standard 3 seconds, preventing "Unknown Interaction" errors.
        await interaction.response.defer(ephemeral=True)
        # -----------------------------------

        user_id = interaction.user.id
        user_member = interaction.user
        bot_config = helper.bot_config
        state = helper.state

        # --- Check 1: Command Disabled ---
        async with state.moderation_lock:
            if user_id in state.omegle_disabled_users:
                await interaction.followup.send(
                    "You are currently disabled from using any commands.",
                    ephemeral=True,
                )
                logger.warning(
                    f"Blocked disabled user {interaction.user.name} from using button command {command}."
                )
                return

        # --- Check 2: Correct Channel ---
        if (
            user_id not in bot_config.ALLOWED_USERS
            and interaction.channel.id != bot_config.COMMAND_CHANNEL_ID
        ):
            await interaction.followup.send(
                f"All commands should be used in <#{bot_config.COMMAND_CHANNEL_ID}>",
                ephemeral=True,
            )
            return

        # --- Check 2.5: Restricted Commands (Report/Shuffle) ---
        if command == "!report":
            is_allowed = user_id in bot_config.ALLOWED_USERS
            is_admin_role = False
            if isinstance(user_member, discord.Member):
                is_admin_role = any(role.name in bot_config.ADMIN_ROLE_NAME for role in user_member.roles)
            
            if not (is_allowed or is_admin_role):
                await interaction.followup.send("⛔ You do not have permission to use this button.", ephemeral=True)
                return

        if command == "!mshuffle":
            if user_id not in bot_config.ALLOWED_USERS:
                await interaction.followup.send("⛔ This button is restricted to Bot Owners.", ephemeral=True)
                return

        # --- Check 3: Camera On (if required) ---
        if user_id not in bot_config.ALLOWED_USERS:
            camera_required_commands = [
                "!skip", "!refresh", "!report", "!rules",
                "!mpauseplay", "!mskip", "!mshuffle", "!mclear",
            ]
            if command in camera_required_commands:
                is_in_vc_with_cam = False
                if isinstance(user_member, discord.Member):
                    streaming_vc = user_member.guild.get_channel(bot_config.STREAMING_VC_ID)
                    is_in_vc_with_cam = bool(
                        streaming_vc
                        and user_member in streaming_vc.members
                        and user_member.voice
                        and user_member.voice.self_video
                    )
                if not is_in_vc_with_cam:
                    await interaction.followup.send(
                        "You must be in the Streaming VC with your camera on to use this button.", 
                        ephemeral=True
                    )
                    return

            # --- Check 3.5: Music Roles ---
            music_commands = ["!mpauseplay", "!mskip", "!mshuffle", "!mclear"]
            if command in music_commands and bot_config.MUSIC_ROLES:
                user_roles = [r.name for r in user_member.roles]
                if not any(role in user_roles for role in bot_config.MUSIC_ROLES):
                    roles_str = ", ".join(bot_config.MUSIC_ROLES)
                    await interaction.followup.send(
                        f"⛔ You need one of the following roles to control music: **{roles_str}**", 
                        ephemeral=True
                    )
                    return

        current_time = time.time()

        # --- Check 4: Global Omegle Cooldown ---
        omegle_global_cooldown_commands = ["!skip", "!refresh", "!report"]
        if command in omegle_global_cooldown_commands:
            async with state.cooldown_lock:
                time_since_last_cmd = current_time - state.last_omegle_command_time
                if time_since_last_cmd < 5.0:
                    msg = f"An Omegle command was used globally. Please wait {5.0 - time_since_last_cmd:.1f}s."
                    await interaction.followup.send(msg, ephemeral=True)
                    return
                state.last_omegle_command_time = current_time

        # --- Check 5: Per-User Button Cooldown ---
        async with state.cooldown_lock:
            if user_id in state.button_cooldowns:
                last_used, warned = state.button_cooldowns[user_id]
                time_left = bot_config.COMMAND_COOLDOWN - (current_time - last_used)
                if time_left > 0:
                    if not warned:
                        await interaction.followup.send(
                            f"{interaction.user.mention}, wait {int(time_left)}s before using another button.",
                            ephemeral=True,
                        )
                        state.button_cooldowns[user_id] = (last_used, True)
                    return
            state.button_cooldowns[user_id] = (current_time, False)

        # --- All Checks Passed ---

        # Send a public announcement message (This goes to the channel, not the interaction response)
        try:
            announcement_content = f"**{interaction.user.display_name}** used `{command}`"
            await interaction.channel.send(announcement_content, delete_after=30.0)
            logger.info(f"Announced button use: {interaction.user.name} used {command}")
        except discord.Forbidden:
            logger.warning(f"Missing permissions to send announcement message in #{interaction.channel.name}")
        except Exception as e:
            logger.error(f"Failed to send button usage announcement: {e}")

        # Record statistics
        try:
            record_command_usage(helper.state.analytics, command)
            record_command_usage_by_user(helper.state.analytics, interaction.user.id, command)
        except Exception as e:
            logger.error(f"Failed to record button command usage in stats: {e}", exc_info=True)

        # --- Execute the Command ---
        try:
            # Create a "mock" Context object
            mock_ctx = type(
                "obj",
                (object,),
                {
                    "author": interaction.user,
                    "channel": interaction.channel,
                    "send": interaction.channel.send,
                    "bot": helper.bot,
                    "guild": interaction.guild,
                    "message": interaction.message,
                    "invoked_with": command.lstrip("!"),
                    "from_button": True,
                },
            )()

            if command == "!skip":
                await helper.omegle_handler.custom_skip(mock_ctx)
            elif command == "!refresh":
                await helper.omegle_handler.refresh(mock_ctx)
            elif command == "!report":
                await helper.omegle_handler.report_user(mock_ctx)
            elif command == "!rules":
                await helper.show_rules(mock_ctx)
            elif command == "!mpauseplay":
                cmd_obj = helper.bot.get_command("mpauseplay")
                if cmd_obj: await cmd_obj.callback(mock_ctx)
            elif command == "!mskip":
                cmd_obj = helper.bot.get_command("mskip")
                if cmd_obj: await cmd_obj.callback(mock_ctx)
            elif command == "!mshuffle":
                cmd_obj = helper.bot.get_command("mshuffle")
                if cmd_obj: await cmd_obj.callback(mock_ctx)
            elif command == "!mclear":
                # !mclear needs the interaction object for its confirmation modal
                # It handles the pre-deferred state internally via followup.
                await helper.confirm_and_clear_music_queue(interaction)
            else:
                await interaction.followup.send("This button action is not yet implemented.", ephemeral=True)
                
        except Exception as invoke_err:
            logger.error(f"Error executing '{command}' from button: {invoke_err}", exc_info=True)
            await interaction.followup.send("An error occurred while running that action.", ephemeral=True)

    except Exception as e:
        logger.error(f"Error in button callback: {e}", exc_info=True)
        # Attempt to send a generic error if possible
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred processing the button click.", ephemeral=True)
            else:
                await interaction.followup.send("An error occurred processing the button click.", ephemeral=True)
        except Exception:
            pass

class HelpButton(Button):
    """A custom button for the Omegle Help View."""

    def __init__(
        self,
        label: str,
        emoji: str,
        command: str,
        style: discord.ButtonStyle,
        helper: "BotHelper",
    ):
        super().__init__(label=label, emoji=emoji, style=style)
        self.command = command
        self.helper = helper

    async def callback(self, interaction: discord.Interaction):
        # Pass the interaction to the central handler
        await _button_callback_handler(interaction, self.command, self.helper)


class MusicButton(Button):
    """A custom button for the Music Control View."""

    def __init__(
        self,
        label: str,
        emoji: str,
        command: str,
        style: discord.ButtonStyle,
        helper: "BotHelper",
    ):
        super().__init__(label=label, emoji=emoji, style=style)
        self.command = command
        self.helper = helper

    async def callback(self, interaction: discord.Interaction):
        # Pass the interaction to the central handler
        await _button_callback_handler(interaction, self.command, self.helper)


class HelpView(View):
    """The persistent View that holds the Omegle control buttons."""

    def __init__(self, helper: "BotHelper"):
        super().__init__(timeout=None)  # Persistent view
        cmds = [
            ("🔄", "👤", "!refresh", discord.ButtonStyle.danger),
            ("⏭️", "👤", "!skip", discord.ButtonStyle.success),
            ("ℹ️", "👤", "!rules", discord.ButtonStyle.primary),
            ("🚩", "👤", "!report", discord.ButtonStyle.secondary),
        ]
        for e, l, c, s in cmds:
            self.add_item(HelpButton(label=l, emoji=e, command=c, style=s, helper=helper))


class QueueDropdown(discord.ui.Select):
    """
    A dropdown menu for the interactive queue (`!q`), allowing users
    to select a song to jump to.
    """

    def __init__(self, bot, state, page_items, author):
        self.bot = bot
        self.state = state
        self.author = author  # Only the user who ran !q can use this
        options = [
            discord.SelectOption(
                label=f"{i + 1}. {song_info.get('title', 'Unknown Title')}"[:100],
                value=str(i),
            )
            for i, song_info in page_items
        ]
        super().__init__(
            placeholder="Select a song to jump to...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        # Check if the interactor is the original command author
        if interaction.user != self.author:
            await interaction.response.send_message(
                "You can't control this menu.", ephemeral=True
            )
            return

        selected_index = int(self.values[0])
        async with self.state.music_lock:
            full_queue = self.state.active_playlist + self.state.search_queue
            if selected_index >= len(full_queue):
                await interaction.response.send_message(
                    "That song is no longer in the queue. The list may be outdated.",
                    ephemeral=True,
                    delete_after=10,
                )
                try:
                    await interaction.message.delete()
                except discord.NotFound:
                    pass
                return

            # Find the selected song and move it to the front of the search queue
            selected_song = full_queue[selected_index]
            try:
                self.state.active_playlist.remove(selected_song)
            except ValueError:
                try:
                    self.state.search_queue.remove(selected_song)
                except ValueError:
                    logger.error(
                        f"FATAL: QueueDropdown song {selected_song.get('title')} not found in any queue for removal."
                    )
                    await interaction.response.send_message(
                        "A queue consistency error occurred.", ephemeral=True
                    )
                    return
            
            # Insert at the front of the search_queue to be played next
            self.state.search_queue.insert(0, selected_song)
            # Set override flag to ensure it plays next even in shuffle
            self.state.play_next_override = True

        # Stop the current song to trigger the 'after' callback
        if self.bot.voice_client_music and self.bot.voice_client_music.is_connected():
            self.bot.voice_client_music.stop()
            await interaction.response.send_message(
                f"✅ Jumping to **{selected_song.get('title')}**.",
                ephemeral=True,
                delete_after=10,
            )
        else:
            await interaction.response.send_message(
                f"✅ Queued **{selected_song.get('title')}** to play next.",
                ephemeral=True,
                delete_after=10,
            )
        
        # Clean up the queue message
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass


class QueueView(discord.ui.View):
    """
    The view for the interactive queue (`!q`), holding the dropdown
    and pagination buttons.
    """

    def __init__(self, bot, state, author):
        super().__init__(timeout=300.0)  # Times out after 5 minutes
        self.bot = bot
        self.state = state
        self.author = author
        self.current_page = 0
        self.page_size = 25  # Max options in a dropdown
        self.full_queue = []
        self.message = None  # To store the message object for editing

    async def start(self):
        """Initializes the queue data and components."""
        await self.update_queue()
        self.update_components()

    async def update_queue(self):
        """Fetches the latest queue from the bot state."""
        async with self.state.music_lock:
            # Get a snapshot of the current queue
            self.full_queue = list(
                enumerate(self.state.active_playlist + self.state.search_queue)
            )
        self.total_pages = (len(self.full_queue) + self.page_size - 1) // self.page_size
        self.total_pages = max(1, self.total_pages)  # At least 1 page

    def get_content(self) -> str:
        """Gets the text content for the queue message."""
        total_songs = len(self.full_queue)
        page_num = self.current_page + 1
        return f"**Current Queue ({total_songs} songs):** Page {page_num}/{self.total_pages}\n*(Select a song to jump to it)*"

    def update_components(self):
        """Rebuilds the view's items (dropdown, buttons) for the current page."""
        self.clear_items()
        
        # Get items for the current page
        start_index = self.current_page * self.page_size
        end_index = start_index + self.page_size
        page_items = self.full_queue[start_index:end_index]

        if page_items:
            self.add_item(
                QueueDropdown(self.bot, self.state, page_items, self.author)
            )

        # Add pagination buttons if needed
        if self.total_pages > 1:
            self.add_item(
                self.create_nav_button("⬅️ Prev", "prev_page", self.current_page == 0)
            )
            self.add_item(
                self.create_nav_button(
                    "Next ➡️", "next_page", self.current_page >= self.total_pages - 1
                )
            )

    def create_nav_button(
        self, label: str, custom_id: str, disabled: bool
    ) -> discord.ui.Button:
        """Helper to create a pagination button."""
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id,
            disabled=disabled,
        )

        async def nav_callback(interaction: discord.Interaction):
            if interaction.user != self.author:
                await interaction.response.send_message(
                    "You can't control this menu.", ephemeral=True
                )
                return
            
            # Update page number
            if interaction.data["custom_id"] == "prev_page":
                self.current_page -= 1
            elif interaction.data["custom_id"] == "next_page":
                self.current_page += 1
            
            # Rebuild and edit the message
            self.update_components()
            await interaction.response.edit_message(
                content=self.get_content(), view=self
            )

        button.callback = nav_callback
        return button

    async def on_timeout(self):
        """Disables all components when the view times out."""
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass


class MusicView(View):
    """The persistent View that holds the music control buttons."""

    def __init__(self, helper: "BotHelper"):
        super().__init__(timeout=None)
        btns = [
            ("⏯️", "🎵", "!mpauseplay", discord.ButtonStyle.danger),
            ("⏭️", "🎵", "!mskip", discord.ButtonStyle.success),
            ("🔀", "🎵", "!mshuffle", discord.ButtonStyle.primary),
            ("❌", "🎵", "!mclear", discord.ButtonStyle.secondary),
        ]
        for e, l, c, s in btns:
            self.add_item(MusicButton(label=l, emoji=e, command=c, style=s, helper=helper))


# --- Main Helper Class ---

class BotHelper:
    """
    Contains implementations for all bot commands and event handlers.
    This class is instantiated by the main bot.py and holds references
    to the bot, state, config, and other handlers.
    """

    def __init__(
        self,
        bot: commands.Bot,
        state: BotState,
        bot_config: BotConfig,
        save_func: Optional[Callable] = None,
        play_next_song_func: Optional[Callable] = None,
        omegle_handler: Optional[OmegleHandler] = None,
        update_menu_func: Optional[Callable] = None,
        trigger_repost_func: Optional[Callable] = None, # <-- ADDED
    ):
        self.bot = bot
        self.state = state
        self.bot_config = bot_config
        self.save_state = save_func
        self.play_next_song = play_next_song_func
        self.omegle_handler = omegle_handler
        self.update_music_menu = update_menu_func
        self.trigger_full_menu_repost = trigger_repost_func # <-- ADDED
        self.LEAVE_BATCH_DELAY_SECONDS = 10  # Batch leave events

    async def _schedule_leave_processing(self):
        """Schedules the leave batch processor to run after a delay."""
        await asyncio.sleep(self.LEAVE_BATCH_DELAY_SECONDS)
        await self._process_leave_batch()

    async def _process_leave_batch(self):
        """
        Processes a batch of member leave events to send a single
        summary embed instead of spamming the chat.
        
        HIGHLIGHTED: Sends to LOG_GC and CHAT_CHANNEL if a user had roles.
        NORMAL: Sends to CHAT_CHANNEL only if user had no roles.
        """
        async with self.state.moderation_lock:
            if not self.state.leave_buffer:
                return
            members_to_announce = self.state.leave_buffer.copy()
            self.state.leave_buffer.clear()
            self.state.leave_batch_task = None

        chat_channel = self.bot.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        log_channel = self.bot.get_channel(self.bot_config.LOG_GC)

        if not chat_channel and not log_channel:
            logger.error("No chat_channel or log_channel found for leave batch processing.")
            return

        count = len(members_to_announce)
        embed = None
        is_highlight_event = False

        if count == 1:
            # Single user leave
            member_data = members_to_announce[0]
            roles_list = member_data.get("roles_list", [])
            role_string = " ".join(roles_list) if roles_list else "No roles"
            has_roles = bool(roles_list)
            is_highlight_event = has_roles

            embed_color = discord.Color.red()
            description = f"{member_data['mention']} **LEFT the SERVER**"

            if has_roles:
                embed_color = discord.Color.dark_red()
                description = f"⚠️{member_data['mention']} **LEFT the SERVER**"

            embed = discord.Embed(
                description=description,
                color=embed_color,
            )
            embed.set_author(name=member_data["name"], icon_url=member_data["avatar_url"])
            if member_data["joined_at"]:
                duration = datetime.now(timezone.utc) - member_data["joined_at"]
                duration_str = format_departure_time(duration)
                embed.add_field(name="Time in Server", value=duration_str, inline=True)
            embed.add_field(name="Roles", value=role_string, inline=True)
        
        else:
            # Mass leave event
            any_had_roles = any(member_data.get("roles_list") for member_data in members_to_announce)
            is_highlight_event = any_had_roles

            embed_color = discord.Color.red()
            title = f"🚪 Mass Departure Event"
            if any_had_roles:
                embed_color = discord.Color.dark_red()
                title = f"⚠️ Mass Departure Event (Included Users With Roles)"

            embed = discord.Embed(
                title=title, color=embed_color
            )
            description_lines = []
            for member_data in members_to_announce[:10]:  # Show up to 10
                duration_str = ""
                if member_data["joined_at"]:
                    duration = datetime.now(timezone.utc) - member_data["joined_at"]
                    duration_str = f" (Stayed for {format_departure_time(duration)})"
                
                roles_list = member_data.get("roles_list", [])
                roles_str = f" [Roles: {len(roles_list)}]" if roles_list else ""

                description_lines.append(f"• {member_data['name']}{duration_str}{roles_str}")
            
            description = "\n".join(description_lines)
            if count > 10:
                description += f"\n...and {count - 10} others."
            
            embed.description = description
            embed.set_footer(text=f"{count} members left the server.")

        async with self.state.moderation_lock:
            notifications_are_enabled = self.state.notifications_enabled
        
        if notifications_are_enabled and embed:
            # Send to LOG_GC if it's a highlight event
            if is_highlight_event and log_channel:
                try:
                    await log_channel.send(embed=embed)
                except discord.Forbidden:
                    logger.warning(f"Failed to send leave notification to LOG_GC: Missing permissions.")
                except Exception as e:
                    logger.error(f"Failed to send leave notification to LOG_GC: {e}")
            
            # FIX: Only send to CHAT_CHANNEL if it's distinct from LOG_GC or if we didn't send there
            sent_to_log = is_highlight_event and log_channel
            should_send_to_chat = chat_channel and (not sent_to_log or chat_channel.id != log_channel.id)

            if should_send_to_chat:
                try:
                    await chat_channel.send(embed=embed)
                except discord.Forbidden:
                    logger.warning(f"Failed to send leave notification to CHAT_CHANNEL: Missing permissions.")
            
        logger.info(f"Processed a batch of {count} member departures. Highlight: {is_highlight_event}")

    async def _log_timeout_in_state(
        self,
        member: discord.Member,
        duration_seconds: int,
        reason: str,
        moderator_name: str,
        moderator_id: Optional[int] = None,
    ):
        """Logs an active timeout to the bot state."""
        async with self.state.moderation_lock:
            self.state.active_timeouts[member.id] = {
                "timeout_end": time.time() + duration_seconds,
                "reason": reason,
                "timed_by": moderator_name,
                "timed_by_id": moderator_id,
                "start_timestamp": time.time(),
            }
            self.state.timeout_wake_event.set()

    async def _create_departure_embed(
        self,
        member_or_user: Union[discord.Member, discord.User],
        moderator: Union[discord.User, str],
        reason: str,
        action: str,
        color: discord.Color,
    ) -> discord.Embed:
        """
        Creates a standardized embed for Kick or Ban announcements.
        """
        mention = getattr(member_or_user, "mention", f"<@{member_or_user.id}>")
        author_name = getattr(member_or_user, "name", "Unknown User")
        avatar_url = (
            member_or_user.display_avatar.url
            if hasattr(member_or_user, "display_avatar")
            and member_or_user.display_avatar
            else None
        )

        if action.upper() == "KICKED":
            description = f"{mention} **was {action.upper()}**"
        else:
            description = f"{mention} **{action.upper()}**"

        embed = discord.Embed(description=description, color=color)
        if avatar_url:
            embed.set_author(name=author_name, icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)

        # Try to fetch user banner
        try:
            user_obj = await self.bot.fetch_user(member_or_user.id)
            if user_obj.banner:
                embed.set_image(url=user_obj.banner.url)
        except Exception:
            pass  # Ignore if banner can't be fetched

        moderator_mention = getattr(moderator, "mention", str(moderator))
        embed.add_field(name="Moderator", value=moderator_mention, inline=True)

        if hasattr(member_or_user, "joined_at") and member_or_user.joined_at:
            duration = datetime.now(timezone.utc) - member_or_user.joined_at
            duration_str = format_departure_time(duration)
            embed.add_field(name="Time in Server", value=duration_str, inline=True)

        if hasattr(member_or_user, "roles"):
            if isinstance(member_or_user, discord.Member):
                roles = [
                    role.mention
                    for role in member_or_user.roles
                    if role.name != "@everyone"
                ]
            else:
                roles = member_or_user.roles  # Handle role string from leave buffer
            
            if roles:
                roles.reverse()
                embed.add_field(name="Roles", value=" ".join(roles), inline=True)
        
        embed.add_field(name="Reason", value=reason, inline=False)
        return embed

    # --- Event Handlers ---

    def get_active_vote_in_channel(self, channel_id: int) -> Optional[int]:
        """Finds the message ID of the most recent active vote in a specific channel."""
        if not hasattr(self.state, 'active_votes') or not self.state.active_votes:
            return None
            
        # Filter votes that belong to this channel
        matches = [
            mid for mid, data in self.state.active_votes.items() 
            if data["channel_id"] == channel_id
        ]
        
        if not matches:
            return None
            
        # Return the largest ID (which corresponds to the newest message)
        return max(matches)

    async def refresh_active_votes(self):
        """
        Iterates through all active votes in state and refreshes the view
        on the actual Discord messages to ensure buttons work after reboot.
        """
        if not self.state.active_votes:
            return

        logger.info(f"Refreshing {len(self.state.active_votes)} active vote messages...")
        
        # We need a fresh view instance. 
        # Since we are inside helper.py, we can access PersistentVoteView directly.
        # We pass 'self' because 'self' IS the helper instance.
        view = PersistentVoteView(self)
        
        ids_to_remove = []

        for message_id, data in self.state.active_votes.items():
            channel_id = data.get("channel_id")
            if not channel_id:
                ids_to_remove.append(message_id)
                continue

            channel = self.bot.get_channel(channel_id)
            if not channel:
                # If channel is None, the bot might not have cached it yet or it was deleted.
                # We skip for now to be safe.
                continue

            try:
                # Fetch the message
                msg = await channel.fetch_message(message_id)
                
                # Editing the message with the view forces Discord to re-bind the buttons
                await msg.edit(view=view)
                logger.info(f"Refreshed buttons for vote message {message_id}")
                
                # Small sleep to prevent rate limits
                await asyncio.sleep(0.5)
                
            except discord.NotFound:
                logger.warning(f"Vote message {message_id} not found. Removing from state.")
                ids_to_remove.append(message_id)
            except discord.Forbidden:
                logger.warning(f"No permission to edit vote message {message_id}.")
            except Exception as e:
                logger.error(f"Failed to refresh vote {message_id}: {e}")

        # Cleanup deleted messages from state
        if ids_to_remove:
            async with self.state.moderation_lock:
                for mid in ids_to_remove:
                    self.state.active_votes.pop(mid, None)
            if self.save_state:
                await self.save_state()

    @handle_errors
    async def start_vote(self, ctx, args: str):
        """!vote command implementation."""
        record_command_usage(self.state.analytics, "!vote")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!vote")

        # Use shlex to handle quoted arguments correctly if needed, but split is fine for mentions
        args_list = args.split()
        if not args_list:
            await ctx.send("Usage: `!vote <hours> <@user1> <@role1> ...`")
            return

        try:
            duration = float(args_list[0])
            if duration <= 0 or duration > 48: raise ValueError
        except ValueError:
            await ctx.send("❌ Invalid duration (0.1 - 48 hours).")
            return

        # --- NEW TARGET COLLECTION LOGIC ---
        targets = {} # {id: Name} (Dicts preserve insertion order in Python 3.7+)
        
        # Regex to identify mentions
        user_pattern = re.compile(r'<@!?(\d+)>')
        role_pattern = re.compile(r'<@&(\d+)>')

        # Iterate through arguments starting from the second one (skipping duration)
        for arg in args_list[1:]:
            
            # 1. Check if it is a User Mention
            u_match = user_pattern.match(arg)
            if u_match:
                uid = int(u_match.group(1))
                member = ctx.guild.get_member(uid)
                if member and not member.bot:
                    if member.id not in targets:
                        targets[member.id] = member.display_name
                continue

            # 2. Check if it is a Role Mention OR Role Name
            role = None
            r_match = role_pattern.match(arg)
            
            if r_match:
                # It's a role mention <@&ID>
                rid = int(r_match.group(1))
                role = ctx.guild.get_role(rid)
            else:
                # It's a text name (e.g., "Admin")
                # We search for the role by name (case insensitive)
                for r in ctx.guild.roles:
                    if r.name.lower() == arg.lower():
                        role = r
                        break
            
            # If a role was found, add its members
            if role:
                # Get non-bot members
                role_members = [m for m in role.members if not m.bot]
                
                # --- SORTING: Alphabetical by Nickname (Display Name) ---
                role_members.sort(key=lambda m: m.display_name.lower())
                
                for member in role_members:
                    # Only add if not already in the list (deduplication)
                    if member.id not in targets:
                        targets[member.id] = member.display_name

        if not targets:
            await ctx.send("❌ No valid users found. Please mention users or roles.")
            return

        # --- Create View & Embed (Same as before) ---
        view = PersistentVoteView(self)
        end_time = datetime.now(timezone.utc) + timedelta(hours=duration)
        end_ts = end_time.timestamp()

        # Build Description
        # REMOVED: List generation logic to keep message short.

        desc = (
            f"**Time Remaining:** <t:{int(end_ts)}:R>\n"
            f"**Total Candidates:** {len(targets)}\n\n"
            f"👇 **Click the button below to vote privately!**"
        )

        embed = discord.Embed(title="🗳️ Smash or Pass Vote", description=desc, color=discord.Color.gold())

        msg = await ctx.send(embed=embed, view=view)

        # SAVE TO STATE
        async with self.state.moderation_lock:
            self.state.active_votes[msg.id] = {
                "channel_id": ctx.channel.id,
                "end_time": end_ts,
                "targets": {str(k): v for k, v in targets.items()},
                "votes": {},
                "duration_hours": duration
            }
        
        if self.save_state: await self.save_state()
        logger.info(f"Started persistent vote {msg.id}")

    async def end_vote(self, message_id: int):
        """Finalizes a vote, shows graphs, and cleans up state."""
        # 1. Check if vote exists in state
        if message_id not in self.state.active_votes: 
            return
        
        data = self.state.active_votes[message_id]
        channel_id = data["channel_id"]
        
        # 2. Robust Channel Fetching (Fixes Reboot Issue)
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                # If cache miss, try fetching from API
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                # Only delete if we receive a definitive error that it's gone/inaccessible
                logger.warning(f"Channel {channel_id} not found or forbidden. Cleaning up vote {message_id}.")
                async with self.state.moderation_lock:
                    self.state.active_votes.pop(message_id, None)
                return
            except Exception as e:
                # If a temporary error (network), log it and RETURN. 
                # Do NOT delete the vote; try again next loop.
                logger.error(f"Temporary error fetching channel for vote {message_id}: {e}")
                return

        # Calculate Results
        targets = data["targets"]
        votes_map = data["votes"]
        results = []

        for tid, name in targets.items():
            t_votes = votes_map.get(tid, {})
            smash = list(t_votes.values()).count("smash")
            pass_ = list(t_votes.values()).count("pass")
            total = smash + pass_
            ratio = (smash / total * 100) if total > 0 else 0
            results.append({"name": name, "smash": smash, "pass": pass_, "total": total, "ratio": ratio})

        # Sort Lists
        most_smashed = sorted(results, key=lambda x: (x['smash'], x['ratio']), reverse=True)
        most_passed = sorted(results, key=lambda x: (x['pass'], -x['ratio']), reverse=True)

        # Bar for Smash (Green -> Red)
        def make_smash_bar(val, max_val):
            if max_val == 0: return "⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜"
            filled = int((val / max_val) * 10)
            return "🟩" * filled + "🟥" * (10 - filled)

        # Bar for Pass (Red -> Green)
        def make_pass_bar(val, max_val):
            if max_val == 0: return "⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜"
            filled = int((val / max_val) * 10)
            return "🟥" * filled + "🟩" * (10 - filled)

        # 1. Smash Results Embed
        embed_s = discord.Embed(title="🔥 Vote Results: Most Smashed", color=discord.Color.green())
        desc_s = []
        
        for i, res in enumerate(most_smashed[:15], 1): 
            bar = make_smash_bar(res['smash'], res['total'] if res['total'] > 0 else 1)
            desc_s.append(f"`#{i}` **{res['name']}**\n{bar} **{int(res['ratio'])}%** ({res['smash']} S / {res['pass']} P)")
        embed_s.description = "\n".join(desc_s) or "No votes cast."

        # 2. Pass Results Embed
        embed_p = discord.Embed(title="🚮 Vote Results: Most Passed", color=discord.Color.red())
        desc_p = []
        
        for i, res in enumerate(most_passed[:15], 1):
            pass_ratio = 100 - res['ratio']
            bar = make_pass_bar(res['pass'], res['total'] if res['total'] > 0 else 1)
            desc_p.append(f"`#{i}` **{res['name']}**\n{bar} **{int(pass_ratio)}%** ({res['smash']} S / {res['pass']} P)")
        embed_p.description = "\n".join(desc_p) or "No votes cast."

        try:
            # Edit original message to show ended
            msg = await channel.fetch_message(message_id)
            orig_embed = msg.embeds[0]
            orig_embed.title = "🔴 Vote Ended"
            orig_embed.color = discord.Color.dark_grey()
            await msg.edit(embed=orig_embed, view=None) # Remove buttons
            
            # Send Results
            await channel.send(embed=embed_s)
            await channel.send(embed=embed_p)
        except Exception as e:
            logger.error(f"Error sending vote results: {e}")

        # Cleanup
        async with self.state.moderation_lock:
            # Use .pop to be safe against key errors
            self.state.active_votes.pop(message_id, None)
        
        if self.save_state: await self.save_state()

    @handle_errors
    async def handle_member_join(self, member: discord.Member) -> None:
        """Called by on_member_join event."""
        if member.guild.id != self.bot_config.GUILD_ID:
            return

        # Send join announcement
        chat_channel = member.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if chat_channel:
            embed = discord.Embed(
                description=f"{member.mention} **JOINED the SERVER**!",
                color=discord.Color.green(),
            )
            embed.set_author(name=member.name, icon_url=member.display_avatar.url)
            embed.set_thumbnail(url=member.display_avatar.url)
            try:
                user_obj = await self.bot.fetch_user(member.id)
                if user_obj and user_obj.banner:
                    embed.set_image(url=user_obj.banner.url)
            except Exception as e:
                logger.warning(
                    f"Could not fetch banner for new member {member.name}: {e}"
                )
            
            embed.add_field(
                name="Account Age", value=get_discord_age(member.created_at), inline=True
            )
            await chat_channel.send(embed=embed)

        # Log join to state
        async with self.state.moderation_lock:
            self.state.recent_joins.append(
                (member.id, member.name, member.display_name, datetime.now(timezone.utc))
            )
        
        logger.info(
            f"{member.name} joined the server {datetime.now().strftime('%m-%d-%Y %H:%M:%S')}."
        )

    @handle_errors
    async def send_punishment_vc_notification(
        self, member: discord.Member, reason: str, moderator_name: str
    ) -> None:
        """Sends an announcement when a user is moved to the punishment VC."""
        chat_channel = member.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel:
            return

        embed = discord.Embed(
            description=f"{member.mention} **was MOVED to the No Cam VC**",
            color=discord.Color.dark_orange(),
        )
        embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            user_obj = await self.bot.fetch_user(member.id)
            if user_obj.banner:
                embed.set_image(url=user_obj.banner.url)
        except Exception as e:
            logger.warning(
                f"Could not fetch user banner for punishment notification: {e}"
            )
            pass
        
        embed.add_field(name="Moved By", value=moderator_name, inline=True)
        final_reason = reason or "No reason provided"
        embed.add_field(name="Reason", value=final_reason, inline=False)
        await chat_channel.send(embed=embed)

    @handle_errors
    async def send_timeout_notification(
        self,
        member: discord.Member,
        moderator: discord.User,
        duration: int,
        reason: str = None,
    ) -> None:
        """Sends an announcement when a user is timed out."""
        chat_channel = member.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel:
            return

        duration_str = format_duration(duration)
        embed = discord.Embed(
            description=f"{member.mention} **was TIMED OUT**",
            color=discord.Color.orange(),
        )
        embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            user_obj = await self.bot.fetch_user(member.id)
            if user_obj.banner:
                embed.set_image(url=user_obj.banner.url)
        except Exception:
            pass
        
        embed.add_field(name="Duration", value=duration_str, inline=True)
        roles = [
            role.mention for role in member.roles if role.name != "@everyone"
        ]
        if roles:
            roles.reverse()
            roles_str = " ".join(roles)
            embed.add_field(name="Roles", value=roles_str, inline=True)
        
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        final_reason = reason or "No reason provided"
        embed.add_field(name="Reason", value=final_reason, inline=False)
        await chat_channel.send(embed=embed)

    @handle_errors
    async def send_timeout_removal_notification(
        self, member: discord.Member, duration: int, reason: str = "Expired Naturally"
    ) -> None:
        """Sends an announcement when a user's timeout is removed."""
        async with self.state.moderation_lock:
            if not self.state.notifications_enabled:
                return
        
        chat_channel = member.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel:
            return

        duration_str = format_duration(duration)
        embed = discord.Embed(
            description=f"{member.mention} **TIMEOUT REMOVED**",
            color=discord.Color.orange(),
        )
        embed.set_author(name=f"{member.name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            user_obj = await self.bot.fetch_user(member.id)
            if user_obj.banner:
                embed.set_image(url=user_obj.banner.url)
        except Exception:
            pass

        embed.add_field(name="Duration", value=duration_str, inline=True)
        
        # Try to parse moderator name from manual removal reason
        if "manually removed by" in reason.lower() or "Timeout removed by" in reason:
            try:
                parts = reason.rsplit("by", 1)
                reason_text = parts[0].strip()
                mod_name = parts[1].strip().lstrip("🛡️").strip()
                mod_member = discord.utils.find(
                    lambda m: m.name == mod_name or m.display_name == mod_name,
                    member.guild.members,
                )
                mod_display = mod_member.mention if mod_member else mod_name
                reason = f"{reason_text} by {mod_display}"
            except Exception as e:
                logger.warning(
                    f"Error processing moderator name for timeout removal: {e}"
                )
        
        embed.add_field(name="Reason", value=f"{reason}", inline=False)
        await chat_channel.send(embed=embed)

    @handle_errors
    async def send_unban_notification(
        self, user: discord.User, moderator: discord.User
    ) -> None:
        """Sends an announcement when a user is unbanned."""
        async with self.state.moderation_lock:
            if not self.state.notifications_enabled:
                return
        
        chat_channel = self.bot.get_guild(self.bot_config.GUILD_ID).get_channel(
            self.bot_config.CHAT_CHANNEL_ID
        )
        if chat_channel:
            embed = discord.Embed(
                description=f"{user.mention} **UNBANNED**",
                color=discord.Color.green(),
            )
            embed.set_author(name=user.name, icon_url=user.display_avatar.url)
            embed.set_thumbnail(url=user.display_avatar.url)
            try:
                user_obj = await self.bot.fetch_user(user.id)
                if user_obj.banner:
                    embed.set_image(url=user_obj.banner.url)
            except Exception:
                pass
            
            embed.add_field(name="Moderator", value=moderator.mention, inline=True)
            await chat_channel.send(embed=embed)
            
            # Log unban to state
            async with self.state.moderation_lock:
                self.state.recent_unbans.append(
                    (user.id, user.name, user.display_name, datetime.now(timezone.utc), moderator.name)
                )
                if len(self.state.recent_unbans) > 100:
                    self.state.recent_unbans.pop(0)

    @handle_errors
    async def handle_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        """
        Called by on_member_ban event.
        This event fires BEFORE on_member_remove. We use it to get ban
        info from the audit log and set a flag in the state.
        """
        logger.info(
            f"handle_member_ban starting for {user.name} ({user.id}) in guild {guild.id}"
        )
        if guild.id != self.bot_config.GUILD_ID:
            logger.warning(
                f"handle_member_ban ignored ban in wrong guild ({guild.id})"
            )
            return

        reason, moderator_name = ("No reason provided", "Unknown")
        try:
            logger.debug(f"Attempting to fetch audit log for ban of {user.name}")
            # Look for the ban action in the audit log
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                logger.debug(
                    f"Audit log entry found: Target={entry.target}, User={entry.user}"
                )
                if entry.target and entry.target.id == user.id:
                    moderator_name = entry.user.name if entry.user else "Unknown"
                    reason = entry.reason or "No reason provided"
                    logger.debug(
                        f"Found matching audit log entry for {user.name}. Mod: {moderator_name}, Reason: {reason}"
                    )
                    break
        except discord.Forbidden:
            logger.error(
                "handle_member_ban failed: Missing permissions to view audit logs."
            )
        except Exception as e:
            logger.error(f"Could not fetch audit log for ban: {e}", exc_info=True)

        try:
            # Log the ban to our internal state
            async with self.state.moderation_lock:
                self.state.recently_banned_ids.add(user.id)
                self.state.recent_bans.append(
                    (
                        user.id,
                        user.name,
                        getattr(user, "display_name", user.name),
                        datetime.now(timezone.utc),
                        reason,
                    )
                )
                logger.info(
                    f"Successfully logged ban state for {user.name}. Reason: {reason}. Moderator: {moderator_name}."
                )
                if self.save_state:
                    # Save state immediately to prevent race condition with on_member_remove
                    asyncio.create_task(self.save_state())
                    logger.debug(
                        "Triggered state save after adding ban ID to fix race condition."
                    )
                    
            asyncio.create_task(self.update_timeouts_report_menu())        
            
        except Exception as state_e:
            logger.critical(
                f"CRITICAL: Failed to update state in handle_member_ban for {user.name}: {state_e}",
                exc_info=True,
            )

    @handle_errors
    async def handle_member_unban(
        self, guild: discord.Guild, user: discord.User
    ) -> None:
        """Called by on_member_unban event."""
        if guild.id != self.bot_config.GUILD_ID:
            return
        
        await asyncio.sleep(4)  # Wait for audit log to update
        
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.unban):
            if entry.target.id == user.id:
                await self.send_unban_notification(user, entry.user)
                return
        
        logger.warning(
            f"Unban for {user.name} detected, but audit log entry not found."
        )
        await self.send_unban_notification(user, self.bot.user)

    @handle_errors
    async def handle_member_remove(self, member: discord.Member) -> None:
        """
        Called by on_member_remove.
        This event fires for leaves, kicks, AND bans. We must
        figure out which one it was.
        """
        if member.guild.id != self.bot_config.GUILD_ID:
            return

        await asyncio.sleep(4)  # Wait for audit logs / ban flag
        
        guild = member.guild
        chat_channel = guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)
        if not chat_channel:
            return

        # --- Check 1: Was this a BAN? ---
        async with self.state.moderation_lock:
            is_banned = member.id in self.state.recently_banned_ids
        
        if is_banned:
            # It was a ban. Fetch audit log to get the moderator and reason.
            # We do this here (instead of handle_member_ban) to ensure we have the full context
            try:
                found_ban_entry = False
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                    if entry.target.id == member.id:
                        found_ban_entry = True
                        reason = entry.reason or "No reason provided"
                        embed = await self._create_departure_embed(
                            member, entry.user, reason, "BANNED", discord.Color.red()
                        )
                        
                        async with self.state.moderation_lock:
                            notifications_are_enabled = self.state.notifications_enabled

                        if notifications_are_enabled:
                            await chat_channel.send(embed=embed)
                        
                        logger.info(f"Processed departure for {member.name} as BAN.")
                        break
                
                if not found_ban_entry:
                    # Fallback if audit log is too slow or missing (uses data from handle_member_ban state if available)
                    logger.warning(f"Ban detected for {member.name} via state, but audit log entry was elusive.")
                    # You could optionally send a generic ban message here if you wish
            
            except Exception as e:
                logger.error(f"Error processing ban notification in remove handler: {e}")

            # CRITICAL: We return here so we don't process this as a 'kick' or 'leave' below
            return

        # --- Check 2: Was this a KICK? ---
        try:
            async for entry in guild.audit_logs(
                limit=20,  # FIX: Increased from 3 to 20 to prevent missing logs in busy servers
                action=discord.AuditLogAction.kick,
                after=member.joined_at or datetime.now(timezone.utc) - timedelta(minutes=5),
            ):
                if (
                    entry.target
                    and entry.target.id == member.id
                ):
                    reason = entry.reason or "No reason provided"
                    embed = await self._create_departure_embed(
                        member, entry.user, reason, "KICKED", discord.Color.orange()
                    )
                    
                    async with self.state.moderation_lock:
                        notifications_are_enabled = self.state.notifications_enabled
                    
                    if notifications_are_enabled:
                        await chat_channel.send(embed=embed)
                    
                    logger.info(f"Processed departure for {member.name} as KICK.")
                    
                    # Log kick to state
                    async with self.state.moderation_lock:
                        roles = [
                            role.mention
                            for role in member.roles
                            if role.name != "@everyone"
                        ]
                        self.state.recent_kicks.append(
                            (
                                member.id,
                                member.name,
                                member.display_name,
                                datetime.now(timezone.utc),
                                reason,
                                entry.user.mention,
                                " ".join(roles),
                            )
                        )
                    asyncio.create_task(self.update_timeouts_report_menu())    
                    return
        except discord.Forbidden:
            logger.warning("Missing permissions to check audit log for kicks.")
        except Exception as e:
            logger.error(f"Error checking audit log for kick: {e}")

        # --- Check 3: This must be a LEAVE ---
        logger.info(f"Buffering LEAVE for {member.name}.")
        roles_list = [
            role.mention for role in member.roles if role.name != "@everyone"
        ]
        roles_list.reverse()
        role_string_for_db = " ".join(roles_list) if roles_list else "No roles"

        # Prep data for the batch processor
        leave_data_for_notification = {
            "mention": member.mention,
            "name": member.name,
            "avatar_url": member.display_avatar.url,
            "joined_at": member.joined_at,
            "roles_list": roles_list,
        }

        # Add to state and schedule the batch processor
        async with self.state.moderation_lock:
            self.state.recent_leaves.append(
                (
                    member.id,
                    member.name,
                    member.display_name,
                    datetime.now(timezone.utc),
                    role_string_for_db,
                )
            )
            if self.state.leave_batch_task:
                self.state.leave_batch_task.cancel()  # Reset timer
            
            self.state.leave_buffer.append(leave_data_for_notification)
            self.state.leave_batch_task = asyncio.create_task(
                self._schedule_leave_processing()
            )

    # --- Command Implementations ---

    async def send_help_menu(self, target: Any) -> None:
        """Sends the persistent Omegle help menu with buttons."""
        try:
            help_description = (
                "\n**Pause** --------- Pause Omegle\n"
                "**Skip** ----------- Skip/Start Omegle\n"
                "**Rules** ---------- Shows Server Rules\n"
                "**Report** -------- Report Omegle User\n"
                "**!commands** --- List All Commands\n"
            )
            embed = discord.Embed(
                title="👤  Omegle Controls  👤",
                description=help_description,
                color=discord.Color.blue(),
            )
            destination = target.channel if hasattr(target, "channel") else target
            if destination and hasattr(destination, "send"):
                await destination.send(embed=embed, view=HelpView(self))
        except Exception as e:
            logger.error(f"Error in send_help_menu: {e}", exc_info=True)

    @handle_errors
    async def show_bans(self, ctx) -> None:
        """!bans command implementation (Sorted Alphabetically by Username)."""
        record_command_usage(self.state.analytics, "!bans")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!bans")
        
        # 1. Fetch bans
        # We use a status message because fetching a large ban list can take a second
        status_msg = await ctx.send("⏳ Fetching ban list...")
        
        ban_entries = [entry async for entry in ctx.guild.bans()]
        if not ban_entries:
            await status_msg.edit(content="No users are currently banned.")
            return

        # 2. Sort alphabetically by username (Case-Insensitive)
        # We use .lower() so 'Zebra' doesn't come before 'apple'
        ban_entries.sort(key=lambda entry: entry.user.name.lower())

        def process_ban(entry):
            user = entry.user
            reason = entry.reason or "No reason provided"
            return f"• `{user.name}` (`{user.id}`) | Reason: *{reason}*"

        embeds = create_message_chunks(
            entries=ban_entries,
            title=f"Banned Users (Total: {len(ban_entries)})",
            process_entry=process_ban,
            as_embed=True,
            embed_color=discord.Color.red(),
        )
        
        # 3. Clean up the status message
        try:
            await status_msg.delete()
        except discord.NotFound:
            pass

        # 4. Send the sorted pages
        for embed in embeds:
            await ctx.send(embed=embed)

    @handle_errors
    async def show_top_members(self, ctx) -> None:
        """!top command implementation."""
        await ctx.send("Gathering member data, this may take a moment...")
        members = list(ctx.guild.members)
        
        # Top 10 by server join date
        joined_members = sorted(
            [m for m in members if m.joined_at], key=lambda m: m.joined_at
        )[:10]
        # Top 10 by account creation date
        created_members = sorted(members, key=lambda m: m.created_at)[:10]

        async def create_member_embed(
            member, rank, color, show_join_date=True
        ):
            """Helper to build a rich embed for a member."""
            user_obj = member
            try:
                fetched_user = await self.bot.fetch_user(member.id)
                if fetched_user:
                    user_obj = fetched_user
            except Exception:
                pass  # Ignore if fetch fails
            
            embed = discord.Embed(
                title=f"#{rank} - {member.display_name}",
                description=f"{member.mention}",
                color=color,
            )
            embed.set_author(
                name=f"{member.name}#{member.discriminator}",
                icon_url=member.display_avatar.url,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            if hasattr(user_obj, "banner") and user_obj.banner:
                embed.set_image(url=user_obj.banner.url)

            embed.add_field(
                name="Account Created",
                value=f"{member.created_at.strftime('%m-%d-%Y')}\n({get_discord_age(member.created_at)} old)",
                inline=True,
            )
            if show_join_date and member.joined_at:
                embed.add_field(
                    name="Joined Server",
                    value=f"{member.joined_at.strftime('%m-%d-%Y')}\n({get_discord_age(member.joined_at)} ago)",
                    inline=True,
                )
            
            roles = [
                role.mention for role in member.roles if role.name != "@everyone"
            ]
            if roles:
                role_str = " ".join(roles)
                if len(role_str) > 1024:
                    role_str = "Too many roles to display."
                embed.add_field(
                    name=f"Roles ({len(roles)})", value=role_str, inline=False
                )
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
        """!info command implementation."""
        command_name = f"!{(ctx.invoked_with if hasattr(ctx, 'invoked_with') else 'info')}"
        record_command_usage(self.state.analytics, command_name)
        record_command_usage_by_user(self.state.analytics, ctx.author.id, command_name)
        
        for msg in self.bot_config.INFO_MESSAGES:
            await ctx.send(msg)

    @handle_errors
    async def list_roles(self, ctx) -> None:
        """!roles command implementation."""
        record_command_usage(self.state.analytics, "!roles")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!roles")
        
        for role in sorted(ctx.guild.roles, key=lambda r: r.position, reverse=True):
            if role.name != "@everyone" and role.members:
                sorted_members = sorted(
                    role.members, key=lambda m: m.name.lower()
                )
                
                def process_member(member):
                    return f"{member.display_name} ({member.name}#{member.discriminator})"
                
                embeds = create_message_chunks(
                    entries=sorted_members,
                    title=f"Role: {role.name}",
                    process_entry=process_member,
                    as_embed=True,
                    embed_color=role.color or discord.Color.default(),
                )
                
                for i, embed in enumerate(embeds):
                    if len(embeds) > 1:
                        embed.title = f"{embed.title} (Part {i + 1})"
                    embed.set_footer(text=f"Total members: {len(role.members)}")
                    await ctx.send(embed=embed)

    @handle_errors
    async def show_role_members(self, ctx, role: discord.Role) -> None:
        """!role <name> command implementation."""
        record_command_usage(self.state.analytics, "!role")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!role")
        
        if not role.members:
            await ctx.send(f"No members found in the **{role.name}** role.")
            return
        
        sorted_members = sorted(role.members, key=lambda m: m.name.lower())
        
        def process_member(member):
            return f"• {member.display_name} ({member.name})"
        
        embeds = create_message_chunks(
            entries=sorted_members,
            title=f"Members in Role: {role.name} (Total: {len(role.members)})",
            process_entry=process_member,
            as_embed=True,
            embed_color=role.color or discord.Color.blue(),
        )
        for embed in embeds:
            await ctx.send(embed=embed)

    @handle_errors
    async def show_admin_list(self, ctx) -> None:
        """!admin command implementation."""
        from tools import build_embed  # Local import to avoid circular dependency
        
        record_command_usage(self.state.analytics, "!admin")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!admin")
        
        guild = ctx.guild
        if not guild:
            return

        # Get Owners (from config.py)
        owners_list = []
        for user_id in self.bot_config.ALLOWED_USERS:
            member = guild.get_member(user_id)
            if member:
                owners_list.append(f"{member.name} ({member.display_name})")
            else:
                try:
                    user = await self.bot.fetch_user(user_id)
                    owners_list.append(
                        f"{user.name} (Not in server, ID: {user_id})"
                    )
                except discord.NotFound:
                    owners_list.append(f"Unknown User (ID: {user_id})")
        
        # Get Admins (from roles in config.py)
        admins_set = set()
        admin_roles = [
            role
            for role in guild.roles
            if role.name in self.bot_config.ADMIN_ROLE_NAME
        ]
        for role in admin_roles:
            for member in role.members:
                if member.id not in self.bot_config.ALLOWED_USERS:
                    admins_set.add(f"{member.name} ({member.display_name})")
        
        owners_text = "\n".join(sorted(owners_list)) if owners_list else "👑 No owners found."
        admins_text = "\n".join(sorted(list(admins_set))) if admins_set else "🛡️ No admins found."
        
        embed_owners = build_embed("👑 Owners", owners_text, discord.Color.gold())
        embed_admins = build_embed("🛡️ Admins", admins_text, discord.Color.red())
        
        await ctx.send(embed=embed_owners)
        await ctx.send(embed=embed_admins)

    @handle_errors
    async def show_commands_list(self, ctx) -> None:
        """!commands command implementation."""
        from tools import build_embed
        
        record_command_usage(self.state.analytics, "!commands")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!commands")
        
        user_commands = (
            "`!skip` - Skips the current Omegle user.\n"
            "`!refresh` - Refreshes the Omegle page.\n"
            "`!info` - Shows server info/rules.\n"
            "`!rules` - Shows the server rules.\n"
            "`!timer <1-60>` - Starts a timer (minutes).\n"
            "`!timerstop` - Stops current user timer.\n"
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
            "`!moff` - Disables all music features and disconnects the bot.\n"
            "`!mon` - Enables all music features and connects the bot.\n"
            "`!rtimeouts` - Removes all active timeouts from users.\n"
            "`!display <user>` - Shows a detailed profile for a user.\n"
            "`!role <@role>` - Lists all members in a specific role.\n"
            "`!move <@user>` - Moves a user from Streaming to Punishment VC.\n"
            "`!commands` - Shows this list of all commands."
        )
        
        allowed_commands = (
            "`!purge <count>` - Purges a specified number of messages.\n"
            "`!help` - Sends the interactive help menu with buttons.\n"
            "`!music` - Sends the interactive music control menu.\n"
            "`!times` - Shows top VC users by time.\n"
            "`!timeouts` - Shows currently timed-out users.\n"
            "`!bans` - Shows currently banned users.\n"
            "`!hush` - Server-mutes all non-admin users in the Streaming VC.\n"
            "`!rhush` / `!removehush` - Removes server-mutes from all users.\n"
            "`!secret` - Server-mutes and deafens all non-admin users.\n"
            "`!rsecret` / `!removesecret` - Removes mute/deafen from all users.\n"
            "`!modoff` / `!modon` - Toggles automated VC moderation.\n"
            "`!disablenotifications` / `!enablenotifications` - Toggles notifications.\n"
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
            "`!vote <roles or users>` - Starts a Smash or Pass vote.\n"
            "`!endvote` - Stops the current Smash or Pass voting system.\n"
            "`!shutdown` - Safely shuts down the bot."
        )
        
        await ctx.send(
            embed=build_embed(
                "👤 User Commands (Camera On)",
                user_commands,
                discord.Color.blue(),
            )
        )
        await ctx.send(
            embed=build_embed(
                "🛡️ Admin Commands (Camera On)",
                admin_commands,
                discord.Color.red(),
            )
        )
        await ctx.send(
            embed=build_embed(
                "👑 Owner Commands (No Requirements)",
                allowed_commands,
                discord.Color.gold(),
            )
        )

    @handle_errors
    async def show_whois(self, ctx) -> None:
        """!whois command implementation."""
        record_command_usage(self.state.analytics, "!whois")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!whois")
        
        now = datetime.now(timezone.utc)
        reports = {}
        has_data = False

        # --- Gather Data (Locked) ---
        async with self.state.moderation_lock:
            time_filter = now - timedelta(hours=24)
            
            # --- ADD THIS LINE BELOW ---
            timeout_data = self.state.active_timeouts.copy() 
            # ---------------------------

            timed_out_members = [
                member for member in ctx.guild.members if member.is_timed_out()
            ]
            untimeout_list = [
                e
                for e in self.state.recent_untimeouts
                if e[3] >= time_filter
                and (len(e) > 5 and e[5] and (e[5] != "System"))
            ]
            kick_list = [e for e in self.state.recent_kicks if e[3] >= time_filter]
            ban_list = [e for e in self.state.recent_bans if e[3] >= time_filter]
            unban_list = [e for e in self.state.recent_unbans if e[3] >= time_filter]
            join_list = [e for e in self.state.recent_joins if e[3] >= time_filter]
            leave_list = [e for e in self.state.recent_leaves if e[3] >= time_filter]
            role_change_list = [
                e for e in self.state.recent_role_changes if e[4] >= time_filter
            ]

        # --- Build User Cache ---
        # Collect all unique user IDs from the gathered data
        user_ids_to_map = {
            entry[0]
            for data_list in [
                untimeout_list,
                kick_list,
                ban_list,
                unban_list,
                join_list,
                leave_list,
                role_change_list,
            ]
            for entry in data_list
        }
        user_map = {}
        if user_ids_to_map:
            ids_to_fetch = set()
            for user_id in user_ids_to_map:
                if (member := ctx.guild.get_member(user_id)):
                    user_map[user_id] = member  # Get from server cache if present
                else:
                    ids_to_fetch.add(user_id)  # Otherwise, mark for fetching
            
            # Fetch users who are not in the server cache (e.g., left/banned)
            if ids_to_fetch:
                async def fetch_user(uid):
                    await asyncio.sleep(0.1)  # Rate limit fetches
                    try:
                        return (uid, await self.bot.fetch_user(uid))
                    except discord.NotFound:
                        return (uid, None)
                    except Exception as e:
                        logger.warning(
                            f"Could not fetch user {uid} for whois report: {e}"
                        )
                        return (uid, None)
                
                fetch_tasks = [fetch_user(uid) for uid in ids_to_fetch]
                results = await asyncio.gather(*fetch_tasks)
                for uid, user_obj in results:
                    user_map[uid] = user_obj

        # --- Helper Functions for Processing ---
        def get_clean_mention(identifier):
            """Returns a mention string from an ID or name."""
            if identifier is None:
                return "Unknown"
            if isinstance(identifier, int):
                return f"<@{identifier}>"
            return str(identifier)

        def get_user_display_info(
            user_id, stored_username=None, stored_display_name=None
        ):
            """Gets a user's mention/name, falling back to stored names."""
            user = user_map.get(user_id)
            if user:
                return f"{user.mention} ({user.name})"
            name = stored_username or "Unknown User"
            return f"`{name}` <@{user_id}>"

        # --- Process Data into Reports ---
        if timed_out_members:
            has_data = True
            def process_timeout(member):
                data = timeout_data.get(member.id, {})
                timed_by = data.get("timed_by_id", data.get("timed_by"))
                reason = data.get("reason")  # <-- Get the reason
                start_ts = data.get("start_timestamp")
                line = f"• {member.mention}"
                if timed_by and timed_by != "Unknown":
                    line += f" by {get_clean_mention(timed_by)}"
                if reason and reason != "No reason provided":  # <-- Add the reason
                    line += f" for *{reason}*"
                if start_ts:
                    line += f" <t:{int(start_ts)}:R>"
                return line

            reports["⏳ Timed Out Members"] = create_message_chunks(
                timed_out_members,
                "⏳ Timed Out Members",
                process_timeout,
                as_embed=False,
            )

        if untimeout_list:
            has_data = True
            def process_untimeout(entry):
                uid, _, _, ts, _, mod_name, mod_id = entry
                mod_mention = get_clean_mention(mod_id or mod_name)
                return f"• <@{uid}> - by {mod_mention} <t:{int(ts.timestamp())}:R>"

            reports["🔓 Recent Untimeouts"] = create_message_chunks(
                untimeout_list,
                "🔓 Recent Untimeouts (24h)",
                process_untimeout,
                as_embed=True,
                embed_color=discord.Color.from_rgb(173, 216, 230),
            )

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

            reports["👢 Recent Kicks"] = create_message_chunks(
                kick_list,
                "👢 Recent Kicks (24h)",
                process_kick,
                as_embed=True,
                embed_color=discord.Color.orange(),
            )

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

            reports["🔨 Recent Bans"] = create_message_chunks(
                ban_list,
                "🔨 Recent Bans (24h)",
                process_ban,
                as_embed=True,
                embed_color=discord.Color.dark_red(),
            )

        if unban_list:
            has_data = True
            def process_unban(entry):
                uid, name, dname, ts, mod = entry
                user_info = get_user_display_info(uid, name, dname)
                return f"• {user_info} - by {mod} <t:{int(ts.timestamp())}:R>"

            reports["🔓 Recent Unbans"] = create_message_chunks(
                unban_list,
                "🔓 Recent Unbans (24h)",
                process_unban,
                as_embed=True,
                embed_color=discord.Color.dark_green(),
            )

        if role_change_list:
            has_data = True
            def process_role_change(entry):
                uid, name, gained, lost, ts = entry
                user_info = get_user_display_info(uid, name)
                parts = [f"• {user_info} <t:{int(ts.timestamp())}:R>"]
                if gained:
                    parts.append(f"  - **Gained**: {', '.join(gained)}")
                if lost:
                    parts.append(f"  - **Lost**: {', '.join(lost)}")
                return parts  # Returns a list of lines

            reports["🎭 Recent Role Changes"] = create_message_chunks(
                role_change_list,
                "🎭 Recent Role Changes (24h)",
                process_role_change,
                as_embed=True,
                embed_color=discord.Color.purple(),
            )

        if join_list:
            has_data = True
            def process_join(entry):
                uid, name, dname, ts = entry
                user_info = get_user_display_info(uid, name, dname)
                return f"• {user_info} <t:{int(ts.timestamp())}:R>"

            reports["🎉 Recent Joins"] = create_message_chunks(
                join_list,
                "🎉 Recent Joins (24h)",
                process_join,
                as_embed=True,
                embed_color=discord.Color.green(),
            )

        if leave_list:
            has_data = True
            def process_leave(entry):
                uid, name, dname, ts, _ = entry
                user_info = get_user_display_info(uid, name, dname)
                return f"• {user_info} <t:{int(ts.timestamp())}:R>"

            reports["🚪 Recent Leaves"] = create_message_chunks(
                leave_list,
                "🚪 Recent Leaves (24h)",
                process_leave,
                as_embed=True,
                embed_color=discord.Color.red(),
            )

        # --- Send Reports ---
        if not has_data:
            await ctx.send("📭 No recent activity found in the last 24 hours.")
            return

        report_order = [
            "⏳ Timed Out Members",
            "🔓 Recent Untimeouts",
            "👢 Recent Kicks",
            "🔨 Recent Bans",
            "🔓 Recent Unbans",
            "🎭 Recent Role Changes",
            "🎉 Recent Joins",
            "🚪 Recent Leaves",
        ]
        
        for report_type in report_order:
            if report_type in reports:
                for chunk in reports[report_type]:
                    if isinstance(chunk, discord.Embed):
                        await ctx.send(embed=chunk)
                    else:
                        await ctx.send(chunk)
                    await asyncio.sleep(0.5)  # Avoid rate limits

    @handle_errors
    async def remove_timeouts(self, ctx) -> None:
        """!rtimeouts command implementation."""
        record_command_usage(self.state.analytics, "!rtimeouts")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!rtimeouts")
        
        timed_out_members = [m for m in ctx.guild.members if m.is_timed_out()]
        if not timed_out_members:
            await ctx.send("No users are currently timed out.")
            return

        # --- Confirmation Step ---
        confirm_msg = await ctx.send(
            f"⚠️ **WARNING:** This will remove timeouts from {len(timed_out_members)} members!\n"
            "React with ✅ to confirm or ❌ to cancel within 30 seconds."
        )
        for emoji in ["✅", "❌"]:
            await confirm_msg.add_reaction(emoji)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in ["✅", "❌"]
                and (reaction.message.id == confirm_msg.id)
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=30.0, check=check
            )
            if str(reaction.emoji) == "❌":
                await ctx.send("Command cancelled.")
                return
        except asyncio.TimeoutError:
            await ctx.send("⌛ Command timed out. No changes were made.")
            return
        # --- End Confirmation ---

        removed, failed = ([], [])
        for member in timed_out_members:
            try:
                await member.timeout(
                    None, reason=f"Timeout removed by {ctx.author.name} ({ctx.author.id})"
                )
                removed.append(member.name)
                # Log to state for !whois
                async with self.state.moderation_lock:
                    if member.id in self.state.active_timeouts:
                        self.state.recent_untimeouts.append(
                            (
                                member.id,
                                member.name,
                                member.display_name,
                                datetime.now(timezone.utc),
                                f"Manually removed by {ctx.author.name}",
                                ctx.author.name,
                                ctx.author.id,
                            )
                        )
                        del self.state.active_timeouts[member.id]
                logger.info(
                    f"Removed timeout from {member.name} by {ctx.author.name}"
                )
            except discord.Forbidden:
                failed.append(f"{member.name} (Missing Permissions)")
            except discord.HTTPException as e:
                failed.append(f"{member.name} (Error: {e})")

        # Send summary
        result_msg = []
        if removed:
            result_msg.append(
                f"**✅ Removed timeouts from:**\n- " + "\n".join(removed)
            )
        if failed:
            result_msg.append(
                f"\n**❌ Failed to remove timeouts from:**\n- " + "\n".join(failed)
            )
        if result_msg:
            await ctx.send("\n".join(result_msg))
        
        # Announce in chat
        if (chat_channel := ctx.guild.get_channel(self.bot_config.CHAT_CHANNEL_ID)):
            await chat_channel.send(
                f"⏰ **Mass Timeout Removal**\nExecuted by {ctx.author.mention}\n"
                f"Removed: {len(removed)} | Failed: {len(failed)}"
            )
        
        asyncio.create_task(self.update_timeouts_report_menu()) # <-- ADDED

    @handle_errors
    async def show_rules(self, ctx) -> None:
        """!rules command implementation."""
        if not getattr(ctx, "from_button", False):
            record_command_usage(self.state.analytics, "!rules")
            record_command_usage_by_user(self.state.analytics, ctx.author.id, "!rules")
        
        await ctx.send("📋 **Server Rules:**\n" + self.bot_config.RULES_MESSAGE)

    async def create_timeouts_report_embed(self) -> Optional[discord.Embed]:
        """
        Builds the embed for the persistent 'Moderation Status' menu.
        """
        guild = self.bot.get_guild(self.bot_config.GUILD_ID)
        if not guild:
            return None

        def get_clean_mention(identifier):
            if identifier is None:
                return "Unknown"
            if isinstance(identifier, int):
                return f"<@{identifier}>"
            return str(identifier)

        embed = discord.Embed(
            title="🛡️ Moderation Status 🛡️",
            color=discord.Color.blue()
        )

        has_data = False # <-- To track if any fields are added

        # --- Field 1: Active Timeouts ---
        timed_out_members = [
            member for member in guild.members if member.is_timed_out()
        ]
        
        # --- Only run if there are timed out members ---
        if timed_out_members:
            has_data = True # Mark that we have data
            timeout_data = {}
            async with self.state.moderation_lock:
                for member in timed_out_members:
                    timeout_data[member.id] = self.state.active_timeouts.get(
                        member.id, {}
                    )
            
            # This is the function we fixed
            def process_timeout(member):
                data = timeout_data.get(member.id, {})
                timed_by = data.get("timed_by_id", data.get("timed_by"))
                reason = data.get("reason")  # <-- Get the reason
                start_ts = data.get("start_timestamp")
                line = f"• {member.mention}"
                if timed_by and timed_by != "Unknown":
                    line += f" by {get_clean_mention(timed_by)}"
                if reason and reason != "No reason provided":  # <-- Check for the reason
                    line += f" for *{reason}*" # <-- Add the reason
                if start_ts:
                    line += f" <t:{int(start_ts)}:R>"
                return line

            active_timeout_lines = [process_timeout(m) for m in timed_out_members]

            embed.add_field(
                name=f"⏳ Active Timeouts",
                value="\n".join(active_timeout_lines),
                inline=False,
            )
        
        # --- Field 2: Command Disabled Users ---
        async with self.state.moderation_lock:
            disabled_user_ids = list(self.state.omegle_disabled_users)
        
        # --- Only run if there are disabled users ---
        if disabled_user_ids:
            has_data = True # Mark that we have data
            
            async def get_disabled_user_line(user_id):
                try:
                    user = await self.bot.fetch_user(user_id)
                    return f"• {user.mention} (`{user.name}`)"
                except discord.NotFound:
                    return f"• Unknown User (ID: `{user_id}`)"
                except Exception:
                    return f"• Error fetching User ID `{user_id}`"

            disabled_user_lines = await asyncio.gather(
                *(get_disabled_user_line(uid) for uid in disabled_user_ids)
            )

            embed.add_field(
                name=f"🚫 Command Disabled",
                value="\n".join(disabled_user_lines),
                inline=False,
            )

        # --- Field 3: Recent Manual Untimeouts ---
        async with self.state.moderation_lock:
            untimeout_entries = [
                e
                for e in self.state.recent_untimeouts
                if len(e) > 5 and e[5] and (e[5] != "System")
            ]
        
        # --- Only run if there are untimeout entries ---
        if untimeout_entries:
            has_data = True # Mark that we have data
            untimeout_lines = []
            processed_users = set()
            unique_untimeout_entries = []
            # Get only the most recent untimeout per user
            for entry in reversed(untimeout_entries):
                if entry[0] not in processed_users:
                    unique_untimeout_entries.append(entry)
                    processed_users.add(entry[0])
            
            for entry in reversed(unique_untimeout_entries[:10]): # Show last 10
                user_id = entry[0]
                ts = entry[3]
                mod_name = entry[5]
                mod_id = entry[6] if len(entry) > 6 else None
                mod_mention = (
                    get_clean_mention(mod_id) if mod_id else get_clean_mention(mod_name)
                )
                line = f"• <@{user_id}>"
                if mod_mention and mod_mention != "Unknown":
                    line += f" by {mod_mention}"
                line += f" <t:{int(ts.timestamp())}:R>"
                untimeout_lines.append(line)

            if untimeout_lines:
                embed.add_field(
                    name=f"🔓 Recent Manual Untimeouts",
                    value="\n".join(untimeout_lines),
                    inline=False,
                )
        
        # --- Add a description if no data was added ---
        if not has_data:
            embed.description = "All moderation systems are clear."

        return embed

    @handle_errors
    async def show_timeouts_report(
        self, destination: Union[commands.Context, discord.TextChannel]
    ) -> Optional[discord.Message]:
        """
        Posts the persistent timeouts report menu.
        Called by bot.py's periodic_menu_update task.
        """
        channel = (
            destination.channel
            if isinstance(destination, commands.Context)
            else destination
        )
        
        embed = await self.create_timeouts_report_embed()
        if embed:
            return await channel.send(embed=embed)
        return None

    @handle_errors
    async def show_timeouts(self, ctx) -> None:
        """!timeouts command implementation."""
        record_command_usage(self.state.analytics, "!timeouts")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!timeouts")
        
        # This command now just generates the embed and sends it as a one-off message.
        embed = await self.create_timeouts_report_embed()
        if embed:
            # Change title for the one-off command
            embed.title = "🛡️ Current Moderation Status 🛡️"
            await ctx.send(embed=embed)
        else:
            await ctx.send("Error generating moderation status report.")


    async def create_times_report_embed(self) -> Optional[discord.Embed]:
        """Generates the embed for the !times report."""
        guild = self.bot.get_guild(self.bot_config.GUILD_ID)
        if not guild:
            return None

        async def get_user_display_info(user_id, data):
            """Helper to get a user's name and highest role."""
            if (member := guild.get_member(user_id)):
                roles = [role for role in member.roles if role.name != "@everyone"]
                highest_role = (
                    max(roles, key=lambda r: r.position) if roles else None
                )
                role_display = f"**[{highest_role.name}]**" if highest_role else ""
                return f"{member.mention} {role_display}"
            username = data.get("username", "Unknown User")
            return f"`{username}` (Left/Not Found)"

        def is_excluded(user_id):
            return user_id in self.bot_config.STATS_EXCLUDED_USERS

        async def get_vc_time_data():
            """Calculates total time, merging saved data and active sessions."""
            async with self.state.vc_lock:
                current_time = time.time()
                # Copy saved data, excluding excluded users
                combined_data = {
                    uid: d.copy()
                    for uid, d in self.state.vc_time_data.items()
                    if not is_excluded(uid)
                }
                total_time_all_users = sum(
                    (d.get("total_time", 0) for d in combined_data.values())
                )

                # Add time from currently active sessions
                for user_id, start_time in self.state.active_vc_sessions.items():
                    if is_excluded(user_id):
                        continue
                    active_duration = current_time - start_time
                    if user_id in combined_data:
                        combined_data[user_id]["total_time"] += active_duration
                    else:
                        member = guild.get_member(user_id)
                        combined_data[user_id] = {
                            "total_time": active_duration,
                            "username": member.name if member else "Unknown",
                            "display_name": member.display_name
                            if member
                            else "Unknown",
                        }
                    total_time_all_users += active_duration
            
            # Sort and get top 10
            sorted_users = sorted(
                combined_data.items(),
                key=lambda item: item[1].get("total_time", 0),
                reverse=True,
            )[:10]
            return (total_time_all_users, sorted_users)

        # Get total tracking duration
        total_tracking_seconds = 0
        async with self.state.vc_lock:
            all_start_times = [
                s["start"]
                for d in self.state.vc_time_data.values()
                for s in d.get("sessions", [])
                if "start" in s
            ]
            all_start_times.extend(self.state.active_vc_sessions.values())
            if all_start_times:
                total_tracking_seconds = time.time() - min(all_start_times)

        total_time_all_users, top_vc_users = await get_vc_time_data()

        # Calculate average users
        average_user_count = 0
        if total_tracking_seconds > 60:
            average_user_count = round(total_time_all_users / total_tracking_seconds)

        # --- Build Embed ---
        description_lines = []
        tracking_time_str = format_duration(total_tracking_seconds)
        description_lines.append(f"⏳ **Tracking Started:** {tracking_time_str} ago")
        description_lines.append("")

        if top_vc_users:
            for i, (uid, data) in enumerate(top_vc_users):
                total_s = data.get("total_time", 0)
                time_str = format_duration(total_s)
                display_info = await get_user_display_info(uid, data)
                description_lines.append(
                    f"**{i + 1}.** {display_info}: **{time_str}**"
                )
        else:
            description_lines.append("No VC time data available yet.")

        description_lines.append("")

        if average_user_count > 0:
            description_lines.append(
                f"👥 **Average User Count:** {average_user_count}"
            )

        total_hours = math.ceil(total_time_all_users / 3600)
        total_time_str = f"{total_hours} hours"
        description_lines.append(
            f"⏱ **Total VC Time (All Users):** {total_time_str}"
        )

        embed = discord.Embed(
            title="🏆 Top 10 VC Members 🏆",
            description="\n".join(description_lines),
            color=discord.Color.gold(),
        )
        return embed

    @handle_errors
    async def show_times_report(
        self, destination: Union[commands.Context, discord.TextChannel]
    ) -> Optional[discord.Message]:
        """!times command implementation. Can also be called by other tasks."""
        channel = (
            destination.channel
            if isinstance(destination, commands.Context)
            else destination
        )
        if isinstance(destination, commands.Context):
            record_command_usage(self.state.analytics, "!times")
            record_command_usage_by_user(
                self.state.analytics, destination.author.id, "!times"
            )
        
        embed = await self.create_times_report_embed()
        if embed:
            return await channel.send(embed=embed)
        return None

    @handle_errors
    async def show_analytics_report(
        self, destination: Union[commands.Context, discord.TextChannel]
    ) -> None:
        """!stats command implementation."""
        if isinstance(destination, commands.Context):
            ctx = destination
            channel = ctx.channel
            record_command_usage(self.state.analytics, "!stats")
            record_command_usage_by_user(
                self.state.analytics, ctx.author.id, "!stats"
            )
        else:
            ctx = None
            channel = destination
        
        guild = channel.guild

        # --- Report 1: VC Times ---
        await self.show_times_report(channel)
        await channel.send("\n" + "─" * 50 + "\n")

        # --- Helper Functions ---
        async def get_user_display_info(user_id):
            """Gets user mention and highest role."""
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                if (member := guild.get_member(user_id)):
                    roles = [
                        role for role in member.roles if role.name != "@everyone"
                    ]
                    highest_role = (
                        max(roles, key=lambda r: r.position) if roles else None
                    )
                    role_display = (
                        f"**[{highest_role.name}]**" if highest_role else ""
                    )
                    return f"{member.mention} {role_display} ({member.name})"
                return f"{user.mention} ({user.name})"
            except Exception:
                return f"<@{user_id}> (Unknown User)"

        async def get_user_plain_name(user_id):
            """Gets user's `Name#discriminator`."""
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                return f"`{user.name}#{user.discriminator}`"
            except Exception:
                # Fallback to name from VC data if user left
                async with self.state.vc_lock:
                    vc_data = self.state.vc_time_data.get(user_id, {})
                    username = vc_data.get("username", f"ID: {user_id}")
                return f"`{username}` (Left/Not Found)"

        def is_excluded(user_id):
            return user_id in self.bot_config.STATS_EXCLUDED_USERS

        has_any_stats_data = False

        # --- Report 2: Command Usage (Overall) ---
        async with self.state.analytics_lock:
            command_usage_data = self.state.analytics.get("command_usage")
        
        if command_usage_data:
            has_any_stats_data = True
            sorted_commands = sorted(
                command_usage_data.items(), key=lambda x: x[1], reverse=True
            )
            for chunk in create_message_chunks(
                sorted_commands,
                "📊 Overall Command Usage",
                lambda cmd: f"• `{cmd[0]}`: {cmd[1]} times",
                as_embed=True,
                embed_color=discord.Color.blue(),
            ):
                await channel.send(embed=chunk)

        # --- Report 3: Command Usage (By User) ---
        async with self.state.analytics_lock:
            usage_by_user_data = self.state.analytics.get("command_usage_by_user")
        
        if usage_by_user_data:
            has_any_stats_data = True
            filtered_users = [
                (uid, cmds)
                for uid, cmds in usage_by_user_data.items()
                if not is_excluded(uid)
            ]
            sorted_users = sorted(
                filtered_users, key=lambda item: sum(item[1].values()), reverse=True
            )[:10]

            async def process_user_usage(entry):
                uid, cmds = entry
                usage = ", ".join(
                    [
                        f"{c}: {cnt}"
                        for c, cnt in sorted(
                            cmds.items(), key=lambda x: x[1], reverse=True
                        )
                    ]
                )
                user_display = await get_user_plain_name(uid)
                return f"• {user_display}: {usage}"

            if sorted_users:
                processed_entries = await asyncio.gather(
                    *(process_user_usage(entry) for entry in sorted_users)
                )
                for chunk in create_message_chunks(
                    processed_entries,
                    "👤 Top 10 Command Users",
                    lambda x: x,
                    as_embed=True,
                    embed_color=discord.Color.green(),
                ):
                    await channel.send(embed=chunk)

        # --- Report 4: VC Violations ---
        async with self.state.moderation_lock:
            user_violations_data = self.state.user_violations
        
        if user_violations_data:
            has_any_stats_data = True
            filtered_violations = [
                (uid, count)
                for uid, count in user_violations_data.items()
                if not is_excluded(uid)
            ]
            sorted_violations = sorted(
                filtered_violations, key=lambda item: item[1], reverse=True
            )[:10]

            async def process_violation(entry):
                uid, count = entry
                if (member := guild.get_member(uid)):
                    user_display_str = (
                        f"`{member.name}` (`{member.display_name}`)"
                        if member.name != member.display_name
                        else f"`{member.name}`"
                    )
                else:
                    try:
                        user_display_str = (
                            f"`{(await self.bot.fetch_user(uid)).name}` (Left Server)"
                        )
                    except discord.NotFound:
                        user_display_str = f"Unknown User (ID: `{uid}`)"
                return f"• {user_display_str}: {count} violation(s)"

            if sorted_violations:
                processed_entries = await asyncio.gather(
                    *(process_violation(entry) for entry in sorted_violations)
                )
                for chunk in create_message_chunks(
                    processed_entries,
                    "⚠️ No-Cam Detected Report",
                    lambda x: x,
                    as_embed=True,
                    embed_color=discord.Color.orange(),
                ):
                    await channel.send(embed=chunk)

        if not has_any_stats_data:
            await channel.send("📊 No command/violation statistics available yet.")

    @handle_errors
    async def send_join_invites(self, ctx) -> None:
        """!join command implementation."""
        record_command_usage(self.state.analytics, "!join")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!join")
        
        guild = ctx.guild
        admin_role_names = self.bot_config.ADMIN_ROLE_NAME
        join_message = self.bot_config.JOIN_INVITE_MESSAGE
        
        admin_roles = [
            role for role in guild.roles if role.name in admin_role_names
        ]
        if not admin_roles:
            await ctx.send("No admin roles found with the specified names.")
            return

        # Get unique set of members with any of the admin roles
        members_to_dm = {member for role in admin_roles for member in role.members}
        if not members_to_dm:
            await ctx.send("No members with the specified admin roles found to DM.")
            return

        await ctx.send(
            f"Sending invites to {len(members_to_dm)} member(s) with the role(s): {', '.join(admin_role_names)}. This may take a moment..."
        )
        
        impacted = []
        for member in members_to_dm:
            if member.bot:
                continue
            try:
                await member.send(join_message)
                impacted.append(member.name)
                logger.info(f"Sent join invite to {member.name}.")
                await asyncio.sleep(1)  # Avoid rate limits
            except discord.Forbidden:
                logger.warning(
                    f"Could not DM {member.name} (DMs are disabled or bot is blocked)."
                )
            except Exception as e:
                logger.error(f"Error DMing {member.name}: {e}")
        
        if impacted:
            msg = "Finished sending invites. Sent to: " + ", ".join(impacted)
            logger.info(msg)
            await ctx.send(msg)
        else:
            await ctx.send("Finished processing. No invites were successfully sent.")

    @handle_errors
    async def clear_whois_data(self, ctx) -> None:
        """!clearwhois command implementation."""
        confirm_msg = await ctx.send(
            "⚠️ This will reset ALL historical event data for `!whois` (joins, leaves, bans, etc.). This cannot be undone.\n"
            "React with ✅ to confirm or ❌ to cancel."
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in ["✅", "❌"]
                and (reaction.message.id == confirm_msg.id)
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=30.0, check=check
            )
            if str(reaction.emoji) == "✅":
                # Clear all history lists in the state
                async with self.state.moderation_lock:
                    self.state.recent_joins.clear()
                    self.state.recent_leaves.clear()
                    self.state.recent_bans.clear()
                    self.state.recent_kicks.clear()
                    self.state.recent_unbans.clear()
                    self.state.recent_untimeouts.clear()
                    self.state.recent_role_changes.clear()
                
                await ctx.send("✅ All `!whois` historical data has been reset.")
                logger.info(
                    f"`!whois` data cleared by {ctx.author.name} (ID: {ctx.author.id})"
                )
                
                # --- NEW LINE ADDED BELOW ---
                asyncio.create_task(self.update_timeouts_report_menu())
                # ----------------------------

                if self.save_state:
                    await self.save_state()
            else:
                await ctx.send("❌ Whois data reset cancelled.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Command timed out. No changes were made.")
        finally:
            try:
                await confirm_msg.delete()
            except Exception:
                pass

    @handle_errors
    async def clear_stats(self, ctx) -> None:
        """!clearstats command implementation."""
        confirm_msg = await ctx.send(
            "⚠️ This will reset all statistics data (VC times, command usage, and violation counts). This does not affect moderation history.\n"
            "React with ✅ to confirm or ❌ to cancel."
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in ["✅", "❌"]
                and (reaction.message.id == confirm_msg.id)
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=30.0, check=check
            )
            if str(reaction.emoji) == "✅":
                # Get current members in VC to restart their sessions
                guild = ctx.guild
                streaming_vc = guild.get_channel(self.bot_config.STREAMING_VC_ID)
                current_members = []
                if streaming_vc:
                    current_members.extend(
                        [m for m in streaming_vc.members if not m.bot]
                    )
                if self.bot_config.ALT_VC_ID:
                    for vc_id in self.bot_config.ALT_VC_ID:
                        if (alt_vc := guild.get_channel(vc_id)):
                            current_members.extend(
                                [m for m in alt_vc.members if not m.bot]
                            )
                
                # Reset all stats
                async with self.state.vc_lock, self.state.analytics_lock, self.state.moderation_lock, self.state.cooldown_lock:
                    self.state.vc_time_data = {}
                    self.state.active_vc_sessions = {}
                    self.state.camera_off_timers = {}
                    self.state.analytics = {
                        "command_usage": {},
                        "command_usage_by_user": {},
                        "violation_events": 0,
                    }
                    self.state.user_violations = {}
                    self.state.recently_logged_commands.clear()
                    
                    # Restart sessions for current members
                    if current_members:
                        current_time = time.time()
                        for member in current_members:
                            self.state.active_vc_sessions[member.id] = current_time
                            self.state.vc_time_data[member.id] = {
                                "total_time": 0,
                                "sessions": [],
                                "username": member.name,
                                "display_name": member.display_name,
                            }
                        logger.info(
                            f"Restarted VC tracking for {len(current_members)} current members"
                        )
                
                await ctx.send("✅ All statistics data have been reset.")
                logger.info(
                    f"Statistics cleared by {ctx.author.name} (ID: {ctx.author.id})"
                )
                
                # Save the cleared state
                if hasattr(self, "save_state") and callable(self.save_state):
                    await self.save_state()
                elif hasattr(self.bot, "save_state_async"):
                    asyncio.create_task(self.bot.save_state_async())
            else:
                await ctx.send("❌ Statistics reset cancelled.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Command timed out. No changes were made.")
        finally:
            try:
                await confirm_msg.delete()
            except Exception:
                pass

    @handle_errors
    async def show_user_display(self, ctx, member: discord.Member) -> None:
        """!display command implementation."""
        record_command_usage(self.state.analytics, "!display")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!display")
        
        user_obj = member
        try:
            # Fetch user to get banner
            fetched_user = await self.bot.fetch_user(member.id)
            if fetched_user:
                user_obj = fetched_user
        except Exception:
            pass
        
        embed = discord.Embed(
            description=f"{member.mention}", color=discord.Color.blue()
        )
        author_name = (
            member.name
            if member.discriminator == "0"
            else f"{member.name}#{member.discriminator}"
        )
        embed.set_author(name=author_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        if hasattr(user_obj, "banner") and user_obj.banner:
            embed.set_image(url=user_obj.banner.url)

        embed.add_field(
            name="Account Created",
            value=f"{member.created_at.strftime('%m-%d-%Y')}\n({get_discord_age(member.created_at)} old)",
            inline=True,
        )
        if member.joined_at:
            embed.add_field(
                name="Joined Server",
                value=f"{member.joined_at.strftime('%m-%d-%Y')}\n({get_discord_age(member.joined_at)} ago)",
                inline=True,
            )
        embed.add_field(name="User ID", value=str(member.id), inline=False)
        
        roles = [
            role.mention for role in member.roles if role.name != "@everyone"
        ]
        if roles:
            roles.reverse()
            role_str = " ".join(roles)
            if len(role_str) > 1024:
                role_str = "Too many roles to display."
            embed.add_field(name=f"Roles", value=role_str, inline=False)
        
        await ctx.send(embed=embed)

    async def create_music_menu_embed_and_view(
        self,
    ) -> Tuple[Optional[discord.Embed], Optional[View]]:
        """
        Builds the music menu embed and view based on the current music state.
        """
        if not self.state.music_enabled:
            return (None, None)
        
        try:
            status_lines = []
            async with self.state.music_lock:
                # Determine playback status
                if self.state.is_music_playing and self.state.current_song:
                    status_lines.append(
                        f"**Now Playing:** `{self.state.current_song['title']}`"
                    )
                elif self.state.is_music_paused and self.state.current_song:
                    status_lines.append(
                        f"**Paused:** `{self.state.current_song['title']}`"
                    )
                else:
                    status_lines.append("**Now Playing:** Nothing")

                # Mode
                current_mode_str = self.state.music_mode.capitalize()
                status_lines.append(f"**Mode:** {current_mode_str}")

                # Volume
                display_volume = 0
                if self.bot_config.MUSIC_MAX_VOLUME > 0:
                    display_volume = int(
                        self.state.music_volume
                        / self.bot_config.MUSIC_MAX_VOLUME
                        * 100
                    )
                status_lines.append(f"**Volume:** {display_volume}%")

                # Queue size
                queue = self.state.active_playlist + self.state.search_queue
                if queue:
                    status_lines.append(f"**Queue:** {len(queue)} song(s)")

            # Build embed description
            description = (
                f"\n**!m song or URL** -------- Find/queue a song\n"
                f"**!q** ---------------------- View the queue\n"
                f"**!np** --------------------- Show current song\n"
                f"**!mclear** ---------------- Clear the Playlist\n"
                f"**!playlist <save/load>** -- Manage playlists\n\n"
                f"*{' | '.join(status_lines)}*\n"
            )
            embed = discord.Embed(
                title="🎵  Music Controls 🎵",
                description=description,
                color=discord.Color.purple(),
            )
            view = MusicView(self)
            return (embed, view)
        except Exception as e:
            logger.error(
                f"Error in create_music_menu_embed_and_view: {e}", exc_info=True
            )
            return (None, None)

    async def send_music_menu(self, target: Any) -> Optional[discord.Message]:
        """Sends the persistent music menu with buttons."""
        try:
            embed, view = await self.create_music_menu_embed_and_view()
            if not embed or not view:
                logger.warning("Failed to create music menu embed/view.")
                return None
            
            destination = target.channel if hasattr(target, "channel") else target
            if destination and hasattr(destination, "send"):
                message = await destination.send(embed=embed, view=view)
                return message
            else:
                logger.warning(
                    f"Unsupported target type for music menu: {type(target)}"
                )
                return None
        except Exception as e:
            logger.error(f"Error in send_music_menu: {e}", exc_info=True)
            return None

    async def update_timeouts_report_menu(self) -> None: # <-- NEW
        """
        Updates the persistent 'Moderation Status' menu in-place.
        """
        if not hasattr(self.state, 'timeouts_report_message_id') or not self.state.timeouts_report_message_id:
            logger.debug("Skipping timeouts report update: No message ID found in state.")
            return
        
        try:
            channel = self.bot.get_channel(self.bot_config.COMMAND_CHANNEL_ID)
            if not channel:
                logger.warning(f"Cannot update timeouts report: Command channel {self.bot_config.COMMAND_CHANNEL_ID} not found.")
                return
            
            message_to_edit = await channel.fetch_message(self.state.timeouts_report_message_id)
            new_embed = await self.create_timeouts_report_embed()
            
            if new_embed:
                await message_to_edit.edit(embed=new_embed)
                logger.info("Successfully updated the persistent !timeouts report.")
            
        except discord.NotFound:
            logger.info('Timeouts report message not found for update. Clearing ID.')
            self.state.timeouts_report_message_id = None
            if self.trigger_full_menu_repost:
                logger.warning('Triggering full menu repost due to missing timeouts report.')
                asyncio.create_task(self.trigger_full_menu_repost())
            else:
                logger.error('Cannot trigger full menu repost: trigger_repost_func not provided to BotHelper.')
                
        except discord.Forbidden:
            logger.warning(f'Lacking permissions to edit the timeouts report message in #{channel.name}.')
            self.state.timeouts_report_message_id = None
            
        except Exception as e:
            logger.error(f'Failed to update persistent timeouts report: {e}', exc_info=True)

    @handle_errors
    async def confirm_and_clear_music_queue(
        self, ctx_or_interaction: Union[commands.Context, discord.Interaction]
    ) -> None:
        """
        !mclear command implementation.
        Uses a confirmation modal because it's a destructive action.
        """
        # --- Context setup (handles both !mclear and button press) ---
        if isinstance(ctx_or_interaction, commands.Context):
            ctx = ctx_or_interaction
            author = ctx.author
            send_func = ctx.send
        elif isinstance(ctx_or_interaction, discord.Interaction):
            interaction = ctx_or_interaction
            author = interaction.user
            send_func = interaction.followup.send
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        else:
            logger.error(
                f"Unsupported context type for clear queue: {type(ctx_or_interaction)}"
            )
            return
        
        record_command_usage(self.state.analytics, "!mclear")
        record_command_usage_by_user(self.state.analytics, author.id, "!mclear")

        # --- Check if there's anything to clear ---
        async with self.state.music_lock:
            full_queue = self.state.active_playlist + self.state.search_queue
            is_playing = self.bot.voice_client_music and (
                self.bot.voice_client_music.is_playing()
                or self.bot.voice_client_music.is_paused()
            )
            queue_length = len(full_queue)

        if not full_queue and (not is_playing):
            if isinstance(ctx_or_interaction, discord.Interaction):
                await interaction.followup.send(
                    "The music queue is already empty and nothing is playing.",
                    ephemeral=True,
                    delete_after=10,
                )
            else:
                await send_func(
                    "The music queue is already empty and nothing is playing.",
                    delete_after=10,
                )
            return

        # --- Confirmation View ---
        confirm_view = View(timeout=30.0)
        confirmed = asyncio.Future()

        async def confirm_callback(interaction: discord.Interaction):
            if interaction.user != author:
                await interaction.response.send_message(
                    "You cannot confirm this action.", ephemeral=True
                )
                return
            await interaction.response.defer()
            confirmed.set_result(True)
            confirm_view.stop()

        async def cancel_callback(interaction: discord.Interaction):
            if interaction.user != author:
                await interaction.response.send_message(
                    "You cannot cancel this action.", ephemeral=True
                )
                return
            await interaction.response.defer()
            confirmed.set_result(False)
            confirm_view.stop()

        confirm_button = Button(
            label="Confirm Clear", style=discord.ButtonStyle.danger, emoji="✅"
        )
        cancel_button = Button(
            label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌"
        )
        confirm_button.callback = confirm_callback
        cancel_button.callback = cancel_callback
        confirm_view.add_item(confirm_button)
        confirm_view.add_item(cancel_button)

        # Send confirmation message
        confirm_msg = None
        if isinstance(ctx_or_interaction, discord.Interaction):
            confirm_msg = await interaction.followup.send(
                f"Are you sure you want to clear **{queue_length}** songs and stop playback?",
                view=confirm_view,
                ephemeral=True,
                wait=True,
            )
        else:
            confirm_msg = await ctx.send(
                f"Are you sure you want to clear **{queue_length}** songs and stop playback?\nReact with ✅/❌.",
                view=confirm_view,
            )

        await confirm_view.wait()  # Wait for button press or timeout

        try:
            await confirm_msg.edit(view=None)  # Remove buttons
        except Exception:
            pass
        
        # --- Action ---
        if confirmed.done() and confirmed.result() is True:
            was_playing = False
            async with self.state.music_lock:
                self.state.search_queue.clear()
                self.state.active_playlist.clear()
                if self.bot.voice_client_music and (
                    self.bot.voice_client_music.is_playing()
                    or self.bot.voice_client_music.is_paused()
                ):
                    was_playing = True
                    self.state.stop_after_clear = True  # Flag for player
                    self.bot.voice_client_music.stop()
            
            response_text = f"✅ Cleared **{queue_length}** songs from the queue."
            if was_playing:
                response_text += " Queue cleared. Starting local library..."
            
            await ctx_or_interaction.channel.send(response_text)
            logger.info(f"Music queue and playback cleared by {author.name}")
            
            if self.update_music_menu:
                self.update_music_menu()  # Update menu state

            # --- AUTO-START LOCAL LIBRARY ---
            if self.play_next_song:
                # Wait 1 second to let the previous track fully release/disconnect
                await asyncio.sleep(1.0)
                # Manually trigger the next song logic.
                # Since queue is empty, this will default to picking a local song.
                self.play_next_song() # <--- REMOVED asyncio.create_task()
            # -------------------------------
        
        elif confirmed.done() and confirmed.result() is False:
            pass # User cancelled
        else:
            pass # View timed out

    @handle_errors
    async def show_now_playing(self, ctx) -> None:
        """!np command implementation."""
        async with self.state.music_lock:
            if (
                not self.state.current_song
                or not self.bot.voice_client_music
                or (
                    not (
                        self.bot.voice_client_music.is_playing()
                        or self.bot.voice_client_music.is_paused()
                    )
                )
            ):
                await ctx.send("Nothing is currently playing.", delete_after=10)
                return
            
            # Build embed with current song info
            song_info = self.state.current_song
            title = song_info.get("title", "Unknown Title")
            embed = discord.Embed(
                title="🎵", description=f"**{title}**", color=discord.Color.purple()
            )
            
            if not song_info.get("is_stream", False):
                embed.add_field(name="Source", value="Local Library", inline=True)
            else:
                embed.add_field(name="Source", value="Online Stream", inline=True)
            
            display_volume = 0
            if self.bot_config.MUSIC_MAX_VOLUME > 0:
                display_volume = int(
                    self.state.music_volume
                    / self.bot_config.MUSIC_MAX_VOLUME
                    * 100
                )
            embed.add_field(name="Volume", value=f"{display_volume}%", inline=True)
            embed.add_field(
                name="Mode", value=self.state.music_mode.capitalize(), inline=True
            )
        
        await ctx.send(embed=embed)

    @handle_errors
    async def show_queue(self, ctx) -> None:
        """!q command implementation."""
        async with self.state.music_lock:
            if not self.state.active_playlist and (not self.state.search_queue):
                await ctx.send("The music queue is empty.", delete_after=10)
                return
        
        # Create and send the interactive QueueView
        view = QueueView(self.bot, self.state, ctx.author)
        await view.start()
        view.message = await ctx.send(content=view.get_content(), view=view)
        
    @handle_errors
    async def start_user_timer(self, ctx, minutes: Optional[int] = None) -> None:
        """!timer command implementation."""
        record_command_usage(self.state.analytics, "!timer")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!timer")

        # 1. Check if argument was provided
        if minutes is None:
            await ctx.send(f"⚠️ Usage: `!timer <minutes>` (e.g., `!timer 20`).", delete_after=10)
            return

        # 2. Validate Duration (1-60)
        if not 1 <= minutes <= 60:
            await ctx.send("❌ Invalid duration. Please enter a number between 1 and 60.", delete_after=10)
            return

        # 3. Check for existing timer and Register Task
        async with self.state.moderation_lock:
            if ctx.author.id in self.state.active_user_timers:
                await ctx.send(f"{ctx.author.mention} you already have an active timer. Use `!timerstop` to cancel it first.", delete_after=10)
                return
            
            # Store the current task so !timerstop can cancel it later
            self.state.active_user_timers[ctx.author.id] = asyncio.current_task()

        try:
            # 4. Confirm and Start Wait
            await ctx.send(f"✅ Timer set for **{minutes} minutes**. I will ping you when it is up!")
            
            # Sleep for the duration
            await asyncio.sleep(minutes * 60)

            # 5. Timer Finished
            await ctx.send(f"⏰ {ctx.author.mention} your **{minutes} minute** timer is up!")

        except asyncio.CancelledError:
            # This block runs when !timerstop is used
            logger.info(f"Timer for {ctx.author.name} was cancelled via !timerstop.")
            raise # Re-raise to ensure proper task cancellation propagation

        except Exception as e:
            logger.error(f"Error in timer logic: {e}")
            await ctx.send("❌ An error occurred with your timer.", delete_after=10)

        finally:
            # 6. Cleanup: Remove user from active dict when done/cancelled/failed
            async with self.state.moderation_lock:
                self.state.active_user_timers.pop(ctx.author.id, None)

    @handle_errors
    async def stop_user_timer(self, ctx) -> None:
        """!timerstop command implementation."""
        record_command_usage(self.state.analytics, "!timerstop")
        record_command_usage_by_user(self.state.analytics, ctx.author.id, "!timerstop")

        async with self.state.moderation_lock:
            if ctx.author.id not in self.state.active_user_timers:
                await ctx.send(f"{ctx.author.mention} you do not have an active timer running.", delete_after=10)
                return
            
            # Retrieve the task and cancel it
            timer_task = self.state.active_user_timers[ctx.author.id]
            timer_task.cancel()
            
            # Remove from dict immediately (redundant vs finally block, but safe)
            self.state.active_user_timers.pop(ctx.author.id, None)

        await ctx.send(f"✅ {ctx.author.mention} your timer has been cancelled.")