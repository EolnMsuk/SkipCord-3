# bot.py

import asyncio
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta, time as dt_time
from functools import wraps
from typing import Any, Callable, Optional
import discord
import keyboard
import yt_dlp
from discord.ext import commands, tasks
from discord.errors import ClientException
from dotenv import load_dotenv
from loguru import logger
import mutagen
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
try:
    import config
except ImportError:
    logger.critical('CRITICAL: config.py not found. Please create it based on the example.')
    sys.exit(1)
from omegle import OmegleHandler
from helper import BotHelper
from tools import BotConfig, BotState, build_embed, build_role_update_embed, handle_errors, record_command_usage, record_command_usage_by_user
load_dotenv()
try:
    spotify_client_id = os.getenv('SPOTIPY_CLIENT_ID')
    spotify_client_secret = os.getenv('SPOTIPY_CLIENT_SECRET')
    if spotify_client_id and spotify_client_secret:
        auth_manager = SpotifyClientCredentials(client_id=spotify_client_id, client_secret=spotify_client_secret)
        sp = spotipy.Spotify(auth_manager=auth_manager)
        logger.info('Spotify client initialized successfully.')
    else:
        sp = None
        logger.warning('Spotify credentials not found in .env. Spotify links will not work.')
except Exception as e:
    sp = None
    logger.error(f'Failed to initialize Spotify client: {e}')
bot_config = BotConfig.from_config_module(config)
required_settings = ['GUILD_ID', 'COMMAND_CHANNEL_ID', 'CHAT_CHANNEL_ID', 'STREAMING_VC_ID', 'PUNISHMENT_VC_ID', 'OMEGLE_VIDEO_URL', 'EDGE_USER_DATA_DIR']
missing_settings = [setting for setting in required_settings if not getattr(bot_config, setting)]
if missing_settings:
    logger.critical(f"FATAL: The following required settings are missing in config.py: {', '.join(missing_settings)}")
    logger.critical('Please fill them out before starting the bot.')
    sys.exit(1)
state = BotState(config=bot_config)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', help_command=None, intents=intents)
bot.is_fully_ready = False
omegle_handler = OmegleHandler(bot, bot_config)
omegle_handler.state = state
bot.state = state
bot.voice_client_music = None
STATE_FILE = 'data.json'
MUSIC_METADATA_CACHE_FILE = 'music_metadata_cache.json'
MUSIC_METADATA_CACHE = {}
YDL_OPTIONS = {'format': 'bestaudio[ext=m4a]/bestaudio/best', 'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s', 'restrictfilenames': True, 'extract_flat': True, 'nocheckcertificate': True, 'ignoreerrors': True, 'logtostderr': False, 'quiet': True, 'no_warnings': True, 'default_search': 'auto', 'source_address': '0.0.0.0', 'no_playlist_index': True, 'yes_playlist': True}
FFMPEG_OPTIONS_STREAM = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn -loglevel debug -nostdin'}
FFMPEG_OPTIONS_LOUDNORM = {'options': '-vn -loglevel error -af "loudnorm=I=-16:LRA=11:tp=-1.5"'}
def get_display_title_from_path(song_path: str) -> str:
    metadata = MUSIC_METADATA_CACHE.get(song_path)
    if metadata:
        raw_title = metadata.get('raw_title')
        raw_artist = metadata.get('raw_artist')
        if raw_title and raw_artist:
            return f'{raw_title} - {raw_artist}'
        elif raw_title:
            return raw_title
    return os.path.basename(song_path)
@tasks.loop(minutes=57.222)
async def periodic_cleanup():
    try:
        await state.clean_old_entries()
        logger.info('Unified cleanup completed (7-day history/entry limits)')
    except Exception as e:
        logger.error(f'Cleanup error: {e}', exc_info=True)
def _save_state_sync(file_path: str, data: dict) -> None:
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
def _load_state_sync(file_path: str) -> dict:
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)
async def save_state_async() -> None:
    serializable_state = {}
    current_time = time.time()
    async with state.vc_lock, state.analytics_lock, state.moderation_lock, state.music_lock:
        active_vc_sessions_copy = state.active_vc_sessions.copy()
        serializable_state = state.to_dict(guild=bot.get_guild(bot_config.GUILD_ID), active_vc_sessions_to_save=active_vc_sessions_copy, current_time=current_time)
    try:
        if serializable_state:
            await asyncio.to_thread(_save_state_sync, STATE_FILE, serializable_state)
            logger.info('Bot state saved, including active VC sessions.')
    except Exception as e:
        logger.error(f'Failed to save bot state: {e}', exc_info=True)
async def load_state_async() -> None:
    global state
    if os.path.exists(STATE_FILE):
        try:
            data = await asyncio.to_thread(_load_state_sync, STATE_FILE)
            state = BotState.from_dict(data, bot_config)
            bot.state = state
            helper.state = state
            omegle_handler.state = state
            logger.info('Bot state loaded successfully.')
        except Exception as e:
            logger.error(f'Failed to load bot state: {e}', exc_info=True)
            state = BotState(config=bot_config)
            bot.state = state
            omegle_handler.state = state
    else:
        logger.info('No saved state file found, starting with a fresh state.')
        state = BotState(config=bot_config)
        bot.state = state
        helper.state = state
        omegle_handler.state = state
helper = BotHelper(bot, state, bot_config, save_state_async, lambda: asyncio.create_task(play_next_song()), omegle_handler=omegle_handler, update_menu_func=lambda: asyncio.create_task(update_music_menu()), trigger_repost_func=lambda: asyncio.create_task(_trigger_full_menu_repost()))
@tasks.loop(minutes=15.333)
async def periodic_state_save() -> None:
    await save_state_async()
@tasks.loop(minutes=17.444)
async def periodic_geometry_save():
    if omegle_handler:
        geometry = await omegle_handler.get_window_geometry()
        if geometry:
            size, position = geometry
            if position.get('x', -1) >= 0 and position.get('y', -1) >= 0:
                async with state.moderation_lock:
                    if state.window_size != size or state.window_position != position:
                        state.window_size = size
                        state.window_position = position
@tasks.loop(seconds=9.1)
async def capture_screenshots_task():
    if not state.omegle_enabled or state.is_banned or (not omegle_handler):
        return
    await omegle_handler.capture_and_store_screenshot()
@capture_screenshots_task.before_loop
async def before_capture_screenshots_task():
    await bot.wait_until_ready()
@tasks.loop(seconds=10.2)
async def check_ban_status_task():
    if not state.omegle_enabled or not omegle_handler:
        return
    await omegle_handler.check_for_ban()
@check_ban_status_task.before_loop
async def before_check_ban_status_task():
    await bot.wait_until_ready()
async def update_music_menu():
    if not hasattr(state, 'music_menu_message_id') or not state.music_menu_message_id or (not state.music_enabled):
        return
    try:
        channel = bot.get_channel(bot_config.COMMAND_CHANNEL_ID)
        if not channel:
            return
        message_to_edit = await channel.fetch_message(state.music_menu_message_id)
        new_embed, new_view = await helper.create_music_menu_embed_and_view()
        if new_embed and new_view:
            await message_to_edit.edit(embed=new_embed, view=new_view)
    except discord.NotFound:
        logger.info('Music menu message not found for update. Clearing ID and triggering full menu repost.')
        state.music_menu_message_id = None
        asyncio.create_task(_trigger_full_menu_repost())
    except discord.Forbidden:
        logger.warning(f'Lacking permissions to edit the music menu message in #{channel.name}.')
        state.music_menu_message_id = None
    except Exception as e:
        logger.error(f'Failed to update music menu: {e}', exc_info=True)
@tasks.loop(minutes=2)
async def auto_delete_old_commands():
    try:
        channel = bot.get_channel(bot_config.COMMAND_CHANNEL_ID)
        if not channel:
            return
        one_minute_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
        async for message in channel.history(limit=200, after=datetime.now(timezone.utc) - timedelta(minutes=15)):
            if message.created_at < one_minute_ago:
                if message.author == bot.user and message.embeds:
                    embed_title = message.embeds[0].title
                    if embed_title and embed_title.strip() in ['👤  Omegle Controls  👤', '🎵  Music Controls 🎵', '🏆 Top 10 VC Members 🏆', '🛡️ Moderation Status 🛡️']:
                        continue
                try:
                    await message.delete()
                    await asyncio.sleep(0.5)
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    logger.warning(f'Missing permissions to delete a message in command channel #{channel.name}.')
                    break
                except Exception as e:
                    logger.error(f'Error deleting old command message: {e}')
    except Exception as e:
        logger.error(f'Error in auto_delete_old_commands task: {e}', exc_info=True)
@auto_delete_old_commands.before_loop
async def before_auto_delete_old_commands():
    await bot.wait_until_ready()
@tasks.loop(minutes=9.555)
async def periodic_times_report_update():
    if not hasattr(state, 'times_report_message_id') or not state.times_report_message_id:
        return
    try:
        channel = bot.get_channel(bot_config.COMMAND_CHANNEL_ID)
        if not channel:
            return
        message_to_edit = await channel.fetch_message(state.times_report_message_id)
        new_embed = await helper.create_times_report_embed()
        if new_embed:
            await message_to_edit.edit(embed=new_embed, content=None)
            logger.info('Successfully updated the periodic !times report.')
    except discord.NotFound:
        logger.info('!times report message not found for update. Clearing ID and triggering full menu repost.')
        state.times_report_message_id = None
        asyncio.create_task(_trigger_full_menu_repost())
    except discord.Forbidden:
        logger.warning(f'Lacking permissions to edit the !times report message in #{channel.name}.')
        state.times_report_message_id = None
    except Exception as e:
        logger.error(f'Failed to update periodic !times report: {e}', exc_info=True)
@periodic_times_report_update.before_loop
async def before_periodic_times_report_update():
    await bot.wait_until_ready()

@tasks.loop(minutes=1.111)
async def periodic_timeouts_report_update():
    # This helper function already contains all necessary logic:
    # 1. Checks if state.timeouts_report_message_id exists.
    # 2. Fetches the message.
    # 3. Creates the new embed.
    # 4. Edits the message.
    # 5. Handles all errors, including discord.NotFound and triggering
    #    a full menu repost if the message is missing.
    await helper.update_timeouts_report_menu()

@periodic_timeouts_report_update.before_loop
async def before_periodic_timeouts_report_update():
    await bot.wait_until_ready()

def is_user_in_streaming_vc_with_camera(user: discord.Member) -> bool:
    streaming_vc = user.guild.get_channel(bot_config.STREAMING_VC_ID)
    return bool(streaming_vc and user in streaming_vc.members and user.voice and user.voice.self_video)
async def global_skip() -> None:
    guild = bot.get_guild(bot_config.GUILD_ID)
    if not guild:
        logger.error('Guild not found for global skip.')
        return

    # --- New Announcement Logic ---
    author_name = "Bot Owner" # Default name
    channel = None
    command_name = "!skip"

    try:
        # 1. Find the command channel
        channel = guild.get_channel(bot_config.COMMAND_CHANNEL_ID)
        if not channel:
            logger.warning("Could not find command channel for global skip announcement.")

        # 2. Find a name for the "author"
        if bot_config.ALLOWED_USERS:
            # Get the first ID from your ALLOWED_USERS list
            first_owner_id = list(bot_config.ALLOWED_USERS)[0]
            owner_member = guild.get_member(first_owner_id)

            if owner_member:
                # Use their display name if they are in the server
                author_name = owner_member.display_name
            else:
                # Fallback to fetching their user object if not
                try:
                    owner_user = await bot.fetch_user(first_owner_id)
                    author_name = owner_user.name
                except Exception:
                    logger.warning("Could not fetch owner user for global skip announcement.")

        # 3. Send the announcement (if channel was found)
        if channel:
            announcement_content = f'**{author_name}** used `{command_name}`'
            await channel.send(announcement_content, delete_after=30.0)
            logger.info(f'Announced global hotkey use: {author_name} used {command_name}')

        # 4. Record stats (just like the button does)
        record_command_usage(state.analytics, command_name)
        if bot_config.ALLOWED_USERS:
             first_owner_id = list(bot_config.ALLOWED_USERS)[0]
             record_command_usage_by_user(state.analytics, first_owner_id, command_name)

    except Exception as e:
        # Log the error but don't stop the skip
        logger.error(f"Failed to send global skip announcement: {e}", exc_info=True)
    # --- End of New Logic ---

    # This is the original logic, which still runs
    await omegle_handler.custom_skip() # Passes ctx=None, which is correct
    logger.info('Executed global skip command via hotkey.')
async def global_mskip() -> None:
    async with state.music_lock:
        if not state.music_enabled or not bot.voice_client_music or (not (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused())):
            logger.warning('Global mskip hotkey pressed, but nothing is playing or music is disabled.')
            return
        if state.music_mode == 'loop':
            state.music_mode = 'shuffle'
            logger.info('Loop mode disabled via global hotkey skip. Switched to Shuffle.')
        state.is_music_paused = False
        bot.voice_client_music.stop()
    logger.info('Executed global music skip command via hotkey.')
async def global_mpause() -> None:
    if not state.music_enabled or not bot.voice_client_music or (not bot.voice_client_music.is_connected()):
        logger.warning('Global mpause hotkey pressed, but bot is not in VC or music is disabled.')
        return
    async with state.music_lock:
        if bot.voice_client_music.is_playing():
            bot.voice_client_music.pause()
            state.is_music_paused = True
            state.is_music_playing = False
            logger.info('Executed global music pause command via hotkey.')
        elif bot.voice_client_music.is_paused():
            bot.voice_client_music.resume()
            state.is_music_paused = False
            state.is_music_playing = True
            logger.info('Executed global music resume command via hotkey.')
        else:
            logger.warning('Global mpause hotkey pressed, but nothing is playing or paused.')
    asyncio.create_task(update_music_menu())
async def global_mvolup() -> None:
    if not state.music_enabled or not bot.voice_client_music:
        return
    async with state.music_lock:
        new_volume = round(min(state.music_volume + 0.05, bot_config.MUSIC_MAX_VOLUME), 2)
        state.music_volume = new_volume
        if bot.voice_client_music.source:
            bot.voice_client_music.source.volume = new_volume
    logger.info(f'Volume increased to {int(state.music_volume * 100)}% via hotkey.')
    asyncio.create_task(update_music_menu())
async def global_mvoldown() -> None:
    if not state.music_enabled or not bot.voice_client_music:
        return
    async with state.music_lock:
        new_volume = round(max(state.music_volume - 0.05, 0.0), 2)
        state.music_volume = new_volume
        if bot.voice_client_music.source:
            bot.voice_client_music.source.volume = new_volume
    logger.info(f'Volume decreased to {int(state.music_volume * 100)}% via hotkey.')
    asyncio.create_task(update_music_menu())
async def ensure_voice_connection() -> bool:
    if not state.music_enabled:
        return False
    guild = bot.get_guild(bot_config.GUILD_ID)
    if not guild:
        logger.error('Guild not found, cannot ensure voice connection.')
        return False
    target_vc = guild.get_channel(bot_config.STREAMING_VC_ID)
    if not target_vc or not isinstance(target_vc, discord.VoiceChannel):
        logger.error(f'STREAMING_VC_ID ({bot_config.STREAMING_VC_ID}) is invalid or not a voice channel.')
        return False
    voice_client = guild.voice_client
    if voice_client and voice_client.is_connected() and (voice_client.channel == target_vc):
        logger.debug('Ensure VC: Already connected correctly (Case 1).')
        bot.voice_client_music = voice_client
        return True
    if voice_client and voice_client.is_connected() and (voice_client.channel != target_vc):
        logger.info(f'Ensure VC: Connected to wrong channel ({voice_client.channel.name}). Moving... (Case 2)')
        try:
            await voice_client.move_to(target_vc)
            bot.voice_client_music = voice_client
            logger.info('Ensure VC: Successfully moved.')
            return True
        except Exception as e:
            logger.error(f'Ensure VC: Move failed: {e}. Disconnecting for reconnect attempt.')
            try:
                await voice_client.disconnect(force=True)
            except Exception as dis_e:
                logger.error(f'Ensure VC: Error during disconnect after move failure: {dis_e}')
    logger.info(f'Ensure VC: Attempting connection... (Case 3)')
    error_to_report = None
    try:
        if guild.voice_client and guild.voice_client.is_connected():
            logger.warning('Ensure VC: Bot connected unexpectedly before await connect(). Using existing.')
            bot.voice_client_music = guild.voice_client
            if bot.voice_client_music.channel != target_vc:
                logger.info(f'Ensure VC: Moving unexpected connection from {bot.voice_client_music.channel.name}.')
                await bot.voice_client_music.move_to(target_vc)
            return True
        bot.voice_client_music = await target_vc.connect(reconnect=True, timeout=60.0)
        logger.info(f'Ensure VC: New connection successful to {target_vc.name}.')
        return True
    except ClientException as e:
        logger.debug(f'Ensure VC: Caught ClientException: {type(e).__name__} - {e}')
        if 'Already connected' in str(e):
            logger.warning(f"Ensure VC: Race condition ('Already connected') caught. Retrieving existing client...")
            
            # --- START FIX ---
            # Don't check .is_connected(). Just get the client object.
            # guild.voice_client is the "official" way to get the client for a guild.
            existing_client = guild.voice_client 
            
            if existing_client:
                logger.info('Ensure VC: Successfully retrieved existing voice client.')
                # This is the crucial state sync
                bot.voice_client_music = existing_client 
                
                # Now, just make sure it's in the right channel.
                if bot.voice_client_music.channel != target_vc:
                    logger.warning(f'Ensure VC: Existing client in wrong channel ({bot.voice_client_music.channel.name}). Moving...')
                    try:
                        await bot.voice_client_music.move_to(target_vc)
                        logger.info('Ensure VC: Move successful.')
                    except Exception as move_e:
                        logger.error(f'Ensure VC: Move failed: {move_e}')
                
                return True # We have a client and it's (now) in the right channel
            
            else:
                # This is the state from the logs: "Already connected" but guild.voice_client is None.
                # This is a deep library-level bug. We can't fix it, only break the loop.
                logger.error("Ensure VC: 'Already connected' error, but guild.voice_client is None! This is an inconsistent state.")
                # We must not return True, as we failed.
                error_to_report = e # Let the error reporting logic handle it.
            # --- END FIX ---

        else:
            logger.error(f'Ensure VC: Reporting non-race ClientException.', exc_info=True)
            error_to_report = e
            logger.debug(f'Ensure VC: error_to_report set to: {error_to_report}')
    except Exception as e:
        logger.debug(f'Ensure VC: Caught generic Exception: {type(e).__name__} - {e}')
        logger.error(f'Ensure VC: Reporting generic Exception.', exc_info=True)
        error_to_report = e
        logger.debug(f'Ensure VC: error_to_report set to: {error_to_report}')
    logger.debug(f'Ensure VC: Reached error reporting block. error_to_report = {repr(error_to_report)}')
    if error_to_report:
        logger.debug(f"Ensure VC: Condition 'if error_to_report' is TRUE. Preparing to send message.")
        current_time = time.time()
        async with state.cooldown_lock:
            if current_time - state.last_vc_connect_fail_time > 300:
                state.last_vc_connect_fail_time = current_time
                logger.debug(f'Ensure VC: Cooldown passed. Attempting to send error to LOG_GC.')
                try:
                    log_channel_id = bot_config.LOG_GC
                    if log_channel_id:
                        channel = bot.get_channel(log_channel_id)
                        if channel:
                            await channel.send(f'⚠️ **Music Bot Error:**\nFailed to connect/ensure voice channel connection. I will keep retrying.\n*Please check my permissions in the VC.*\nError: `{error_to_report}`')
                            logger.debug(f'Ensure VC: Successfully sent error message to LOG_GC.')
                        else:
                            logger.error(f'LOG_GC channel (ID: {log_channel_id}) not found.')
                except Exception as send_e:
                    logger.error(f'Failed to send VC connection error to LOG_GC: {send_e}')
            else:
                logger.debug(f'Ensure VC: Error reporting cooldown active. Skipping message send.')
    else:
        logger.debug(f"Ensure VC: Condition 'if error_to_report' is FALSE. Skipping message send.")
    if guild.voice_client and guild.voice_client.is_connected():
        logger.info('Ensure VC: Failsafe check found a valid connection.')
        bot.voice_client_music = guild.voice_client
        if bot.voice_client_music.channel == target_vc:
            return True
        else:
            logger.warning(f'Ensure VC: Failsafe connection in wrong channel ({bot.voice_client_music.channel.name}). Moving.')
            try:
                await bot.voice_client_music.move_to(target_vc)
                return True
            except Exception as final_move_e:
                logger.error(f'Ensure VC: Failsafe move failed: {final_move_e}')
    bot.voice_client_music = None
    return False
async def scan_and_shuffle_music() -> int:
    if not state.music_enabled:
        return 0
    global MUSIC_METADATA_CACHE
    if os.path.exists(MUSIC_METADATA_CACHE_FILE):
        try:
            with open(MUSIC_METADATA_CACHE_FILE, 'r', encoding='utf-8') as f:
                MUSIC_METADATA_CACHE = json.load(f)
            logger.info(f'Loaded {len(MUSIC_METADATA_CACHE)} entries from persistent metadata cache.')
        except Exception as e:
            logger.error(f'Could not load persistent metadata cache: {e}')
            MUSIC_METADATA_CACHE = {}
    if not bot_config.MUSIC_LOCATION or not os.path.isdir(bot_config.MUSIC_LOCATION):
        if bot_config.MUSIC_LOCATION:
            logger.error(f'Music location invalid or not found: {bot_config.MUSIC_LOCATION}')
        return 0
    def _blocking_scan_and_cache():
        supported_files = bot_config.MUSIC_SUPPORTED_FORMATS
        found_songs = []
        local_metadata_cache = MUSIC_METADATA_CACHE.copy()
        for root, _, files in os.walk(bot_config.MUSIC_LOCATION):
            for file in files:
                if file.lower().endswith(supported_files):
                    song_path = os.path.join(root, file)
                    found_songs.append(song_path)
                    try:
                        file_mod_time = os.path.getmtime(song_path)
                        if song_path in local_metadata_cache and local_metadata_cache[song_path].get('mtime') == file_mod_time:
                            continue
                        audio = mutagen.File(song_path, easy=True)
                        raw_artist = audio.get('artist', [''])[0] if audio else ''
                        raw_title = audio.get('title', [''])[0] if audio else ''
                        album = audio.get('album', [''])[0] if audio else ''
                        local_metadata_cache[song_path] = {'artist': re.sub('[^a-z0-9]', '', raw_artist.lower()), 'title': re.sub('[^a-z0-9]', '', raw_title.lower()), 'album': re.sub('[^a-z0-9]', '', album.lower()), 'raw_artist': raw_artist, 'raw_title': raw_title, 'mtime': file_mod_time}
                    except Exception as e:
                        logger.warning(f'Could not read metadata for {song_path}: {e}')
                        if song_path not in local_metadata_cache:
                            local_metadata_cache[song_path] = {'artist': '', 'title': '', 'album': '', 'raw_artist': '', 'raw_title': '', 'mtime': 0}
        return (found_songs, local_metadata_cache)
    logger.info('Starting non-blocking music library scan...')
    found_songs, updated_metadata_cache = await asyncio.to_thread(_blocking_scan_and_cache)
    MUSIC_METADATA_CACHE = updated_metadata_cache
    logger.info('Music library scan complete.')
    async with state.music_lock:
        state.shuffle_queue.clear()
        state.all_songs.clear()
        if not found_songs:
            logger.warning(f'No music files {bot_config.MUSIC_SUPPORTED_FORMATS} found in the specified directory.')
            return 0
        state.all_songs = sorted(found_songs)
        shuffled_songs = found_songs.copy()
        random.shuffle(shuffled_songs)
        state.shuffle_queue = shuffled_songs
        logger.info(f'Loaded and cached {len(state.all_songs)} songs. Shuffled {len(state.shuffle_queue)} into queue.')
    try:
        with open(MUSIC_METADATA_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(MUSIC_METADATA_CACHE, f)
    except Exception as e:
        logger.error(f'Failed to save persistent metadata cache: {e}')
    return len(state.shuffle_queue)

async def _play_song(song_info: dict, ctx: Optional[commands.Context]=None):
    async with state.music_lock:
        state.is_processing_song = True
    if not state.music_enabled:
        async with state.music_lock:
            state.is_music_playing = False
            state.current_song = None
            state.is_processing_song = False
        return
    if not bot.voice_client_music or not bot.voice_client_music.is_connected():
        logger.error('Playback failed: Bot not connected. Halting.')
        async with state.music_lock:
            state.is_music_playing = False
            state.current_song = None
            state.is_processing_song = False
        return
    try:
        source = None
        song_path_or_url = song_info['path']
        song_display_name = song_info['title']
        is_stream = song_info.get('is_stream', False)
        async with state.music_lock:
            volume = state.music_volume
        logger.debug(f'Attempting to process song: {song_info}')
        
        # --- NEW LOGIC TO RESOLVE YOUTUBE URLS ---
        stream_url_to_play = song_path_or_url # Default to original path
        if is_stream and ('youtube.com' in song_path_or_url or 'youtu.be' in song_path_or_url):
            logger.debug(f"YouTube stream detected. Resolving direct URL with yt-dlp...")
            try:
                # Use a modified YDL_OPTIONS for playback (not playlist, get best audio)
                YDL_PLAYBACK_OPTIONS = YDL_OPTIONS.copy()
                YDL_PLAYBACK_OPTIONS['extract_flat'] = False # We need the full info, not flat
                YDL_PLAYBACK_OPTIONS['yes_playlist'] = False # Don't extract playlist, just the one video
                YDL_PLAYBACK_OPTIONS['default_search'] = 'auto'
                YDL_PLAYBACK_OPTIONS['format'] = 'bestaudio[ext=m4a]/bestaudio/best' # Get best audio format

                with yt_dlp.YoutubeDL(YDL_PLAYBACK_OPTIONS) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, song_path_or_url, download=False)
                    
                    if info and 'url' in info:
                        stream_url_to_play = info['url'] # This is the direct audio stream URL
                        song_display_name = info.get('title', song_display_name) # Update title from resolved info
                        logger.debug(f"Successfully resolved direct stream URL.")
                        # Update the title in the bot's state
                        async with state.music_lock:
                             if state.current_song:
                                state.current_song['title'] = song_display_name
                    else:
                        logger.warning(f"yt-dlp did not return a 'url' for {song_path_or_url}")
                        raise ValueError("yt-dlp failed to extract stream URL")
            except Exception as ydl_e:
                logger.error(f"Failed to resolve YouTube stream URL with yt-dlp: {ydl_e}", exc_info=True)
                if ctx:
                    await ctx.send(f"❌ **Playback Error:** Could not resolve stream for `{song_display_name}`. Skipping.", delete_after=15)
                # Manually trigger the next song
                async with state.music_lock:
                    state.is_music_playing = False
                    state.is_processing_song = False
                asyncio.create_task(update_music_menu())
                await asyncio.sleep(2.0)
                asyncio.create_task(play_next_song()) # Skip to next song
                return # Stop executing this function
        # --- END OF NEW LOGIC ---
        
        ffmpeg_options = {'options': '-vn -loglevel error -nostdin'}
        if is_stream:
            logger.info(f'Processing as a stream: {song_display_name}')
            # FIX: Use the global FFMPEG_OPTIONS_STREAM constant
            ffmpeg_options['before_options'] = FFMPEG_OPTIONS_STREAM.get('before_options', '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5')
            try:
                # *** MODIFIED: Use the (potentially new) stream_url_to_play ***
                source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(stream_url_to_play, **ffmpeg_options), volume=volume)
                # We already set the title from yt-dlp, so no need to update it here
            except Exception as e:
                logger.error(f'Failed to create stream audio source for {stream_url_to_play}: {e}', exc_info=True)
                try:
                    logger.warning('Retrying stream without any before_options...')
                    fallback_ffmpeg_options = {'options': '-vn -loglevel error -nostdin'}
                    # *** MODIFIED: Use the (potentially new) stream_url_to_play ***
                    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(stream_url_to_play, **fallback_ffmpeg_options), volume=volume)
                except Exception as fallback_e:
                    logger.critical(f'Fallback stream creation also failed: {fallback_e}')
                    raise ValueError('discord.FFmpegPCMAudio failed.')
        else:
            logger.info(f"Processing as a local file: '{os.path.basename(stream_url_to_play)}'.")
            if state.config.NORMALIZE_LOCAL_MUSIC:
                logger.debug('Normalizing local file audio.')
                # *** MODIFIED: Use stream_url_to_play (which is just song_path_or_url here) ***
                source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(stream_url_to_play, **FFMPEG_OPTIONS_LOUDNORM), volume=volume)
            else:
                logger.debug('Playing local file without normalization.')
                # *** MODIFIED: Use stream_url_to_play (which is just song_path_or_url here) ***
                source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(stream_url_to_play, options='-vn -loglevel error'), volume=volume)
        
        if not source:
            raise ValueError('Audio source creation failed.')
        
        logger.debug('Audio source created successfully. Attempting to play.')
        await asyncio.sleep(0.5)
        bot.voice_client_music.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(after_playback_handler(e), bot.loop))
        async with state.music_lock:
            state.is_processing_song = False
        
        logger.info(f'Now playing: {song_display_name}')
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=song_display_name))
        
        announcement_ctx = None
        async with state.music_lock:
            if state.announcement_context:
                announcement_ctx = state.announcement_context
                state.announcement_context = None
        
        if announcement_ctx:
            await announcement_ctx.send(f'🎵 Now Playing: **{song_display_name}**')
        elif bot_config.MUSIC_DEFAULT_ANNOUNCE_SONGS and ctx:
            await ctx.send(f'🎵 Now Playing: **{song_display_name}**')
        
        asyncio.create_task(update_music_menu())

    except Exception as e:
        logger.critical('CRITICAL FAILURE IN _play_song.', exc_info=True)
        logger.error(f'--> Failed Song Info: {song_info}')
        if ctx:
            await ctx.send(f"❌ **Playback Error:** Could not play `{song_info.get('title', 'Unknown Title')}`. Check logs.", delete_after=15)
        async with state.music_lock:
            state.is_music_playing = False
            state.is_processing_song = False
        asyncio.create_task(update_music_menu())
        await asyncio.sleep(2.0)
        asyncio.create_task(play_next_song())

async def after_playback_handler(error=None):
    if error:
        logger.error(f'FFmpeg process finished with an error object: {error}', exc_info=error)
    asyncio.create_task(play_next_song())
async def start_music_playback():
    if state.music_startup_lock.locked():
        return
    async with state.music_startup_lock:
        if not state.music_enabled:
            return
        if bot.voice_client_music and (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused()):
            return
        logger.info('Attempting to start music playback...')
        if not await ensure_voice_connection():
            logger.error('Could not start music: failed to ensure voice connection.')
            return
        is_queue_empty = False
        async with state.music_lock:
            if not state.shuffle_queue:
                is_queue_empty = True
        if is_queue_empty:
            logger.info('Music queue is empty, rescanning library before playback.')
            await scan_and_shuffle_music()
        await play_next_song()
async def play_next_song(error=None, is_recursive_call=False):
    if not state.music_enabled:
        return
    if error:
        logger.error(f'Error in music player callback: {error}')
    song_to_play_info = None
    ctx_for_playback = None
    needs_library_scan = False
    async with state.music_lock:
        if getattr(state, 'stop_after_clear', False):
            state.stop_after_clear = False
            state.is_music_playing = False
            state.is_music_paused = False
            state.current_song = None
            state.is_processing_song = False
            logger.info('Playback intentionally stopped after queue clear.')
            await bot.change_presence(activity=None)
            asyncio.create_task(update_music_menu())
            return
    if not await ensure_voice_connection():
        logger.critical('Music playback stopped: Could not establish a voice connection.')
        async with state.music_lock:
            state.is_music_playing = False
            state.current_song = None
            state.is_processing_song = False
        return
    async with state.music_lock:
        if state.play_next_override:
            logger.info('Manual override from !q detected. Playing next song in queue.')
            if state.search_queue:
                song_to_play_info = state.search_queue.pop(0)
            elif state.active_playlist:
                song_to_play_info = state.active_playlist.pop(0)
            state.play_next_override = False
        elif state.music_mode == 'loop' and state.current_song:
            song_to_play_info = state.current_song
            logger.info('Looping current song.')
        else:
            user_queue = state.active_playlist + state.search_queue
            if user_queue:
                if state.music_mode == 'shuffle':
                    logger.info('Shuffle mode active. Picking a random song from the user queue.')
                    song_to_play_info = random.choice(user_queue)
                    try:
                        state.active_playlist.remove(song_to_play_info)
                    except ValueError:
                        try:
                            state.search_queue.remove(song_to_play_info)
                        except ValueError:
                            logger.error('Consistency error: Could not find the shuffled chosen song in any queue to remove it.')
                            song_to_play_info = None
                elif state.music_mode == 'alphabetical':
                    logger.info('Alphabetical mode active. Picking the next song by title from the user queue.')
                    sorted_queue = sorted(user_queue, key=lambda s: s.get('title', '').lower())
                    song_to_play_info = sorted_queue[0]
                    try:
                        state.active_playlist.remove(song_to_play_info)
                    except ValueError:
                        try:
                            state.search_queue.remove(song_to_play_info)
                        except ValueError:
                            logger.error('Consistency error: Could not find the alphabetically chosen song in any queue to remove it.')
                            song_to_play_info = None
                elif state.search_queue:
                    song_to_play_info = state.search_queue.pop(0)
                    logger.info(f"Playing next from user search queue (FIFO): {song_to_play_info.get('title')}")
                elif state.active_playlist:
                    song_to_play_info = state.active_playlist.pop(0)
                    logger.info(f"Playing next from active playlist (FIFO): {song_to_play_info.get('title')}")
            if not song_to_play_info:
                if not state.shuffle_queue:
                    needs_library_scan = True
                else:
                    song_path = state.shuffle_queue.pop(0)
                    display_title = get_display_title_from_path(song_path)
                    song_to_play_info = {'path': song_path, 'title': display_title, 'is_stream': False}
                    logger.info(f'Playing next from local library (Default Shuffle): {display_title}')
    if needs_library_scan:
        if is_recursive_call:
            logger.error('Recursive call to play_next_song detected after a failed library scan. Halting to prevent infinite loop.')
            async with state.music_lock:
                state.is_processing_song = False
            return
        logger.info('Local music queue is empty. Rescanning and reshuffling library...')
        await scan_and_shuffle_music()
        await play_next_song(is_recursive_call=True)
        return
    if song_to_play_info:
        ctx_for_playback = song_to_play_info.get('ctx')
        async with state.music_lock:
            state.is_music_playing = True
            state.is_music_paused = False
            state.current_song = song_to_play_info
        await _play_song(song_to_play_info, ctx=ctx_for_playback)
    else:
        async with state.music_lock:
            state.is_music_playing = False
            state.is_music_paused = False
            state.current_song = None
            state.is_processing_song = False
        logger.warning('Music playback finished. All queues and local library are empty.')
        await bot.change_presence(activity=None)
        asyncio.create_task(update_music_menu())
        return
def omegle_command_cooldown(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(ctx, *args, **kwargs):
        current_time = time.time()
        async with state.cooldown_lock:
            time_since_last_cmd = current_time - state.last_omegle_command_time
            if time_since_last_cmd < 5.0:
                try:
                    await ctx.message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
                await ctx.send(f'{ctx.author.mention}, please wait {5.0 - time_since_last_cmd:.1f} more seconds before using this command again.', delete_after=5)
                return
            state.last_omegle_command_time = current_time
        return await func(ctx, *args, **kwargs)
    return wrapper
def require_user_preconditions():
    async def predicate(ctx):
        if ctx.author.id in bot_config.ALLOWED_USERS:
            return True
        async with state.moderation_lock:
            if ctx.author.id in state.omegle_disabled_users:
                await ctx.send('You are currently disabled from using any commands.', delete_after=10)
                return False
        if ctx.channel.id != bot_config.COMMAND_CHANNEL_ID:
            await ctx.send(f'All commands should be used in <#{bot_config.COMMAND_CHANNEL_ID}>.', delete_after=10)
            return False
        if is_user_in_streaming_vc_with_camera(ctx.author):
            return True
        await ctx.send('You must be in the Streaming VC with your camera on to use commands.', delete_after=10)
        return False
    return commands.check(predicate)
def require_admin_preconditions():
    async def predicate(ctx):
        is_allowed = ctx.author.id in bot_config.ALLOWED_USERS
        is_admin_role = isinstance(ctx.author, discord.Member) and any((role.name in bot_config.ADMIN_ROLE_NAME for role in ctx.author.roles))
        if not (is_allowed or is_admin_role):
            await ctx.send('⛔ You do not have permission to use this command.', delete_after=10)
            return False
        if is_allowed:
            return True
        async with state.moderation_lock:
            if ctx.author.id in state.omegle_disabled_users:
                await ctx.send('You are currently disabled from using any commands.', delete_after=10)
                return False
        if ctx.channel.id != bot_config.COMMAND_CHANNEL_ID:
            await ctx.send(f'All commands should be used in <#{bot_config.COMMAND_CHANNEL_ID}>.', delete_after=10)
            return False
        if is_user_in_streaming_vc_with_camera(ctx.author):
            return True
        await ctx.send('You must be in the Streaming VC with your camera on to use commands.', delete_after=10)
        return False
    return commands.check(predicate)
def require_allowed_user():
    async def predicate(ctx):
        if ctx.author.id in bot_config.ALLOWED_USERS:
            return True
        await ctx.send('⛔ This command can only be used by bot owners.')
        return False
    return commands.check(predicate)
async def _handle_stream_vc_join(member: discord.Member):
    async with state.moderation_lock:
        if member.id in state.users_received_rules or member.id in state.users_with_dms_disabled or member.id in state.failed_dm_users:
            return
    try:
        await member.send(bot_config.RULES_MESSAGE)
        async with state.moderation_lock:
            state.users_received_rules.add(member.id)
        logger.info(f'Sent rules to {member.display_name}')
    except discord.Forbidden:
        async with state.moderation_lock:
            state.users_with_dms_disabled.add(member.id)
            state.failed_dm_users.add(member.id)
        logger.warning(f'Could not DM {member.display_name} (DMs disabled or blocked).')
    except Exception as e:
        async with state.moderation_lock:
            state.failed_dm_users.add(member.id)
        logger.error(f'Generic error sending DM to {member.name}: {e}', exc_info=True)
async def _join_camera_failsafe_check(member: discord.Member, config: BotConfig):
    await asyncio.sleep(5)
    guild = member.guild
    if not guild:
        return
    current_member_state = guild.get_member(member.id)
    if not current_member_state or not current_member_state.voice or (not current_member_state.voice.channel):
        return
    is_in_streaming_vc = current_member_state.voice.channel.id == config.STREAMING_VC_ID
    is_camera_on = current_member_state.voice.self_video
    if is_in_streaming_vc and is_camera_on:
        is_server_muted = current_member_state.voice.mute and (not current_member_state.voice.self_mute)
        is_server_deafened = current_member_state.voice.deaf and (not current_member_state.voice.self_deaf)
        if is_server_muted or is_server_deafened:
            logger.info(f'Failsafe triggered for {current_member_state.name}. Camera is on but they are server-muted/deafened. Correcting.')
            try:
                await current_member_state.edit(mute=False, deafen=False)
                logger.info(f'Failsafe successfully unmuted/undeafened {current_member_state.name}.')
            except Exception as e:
                logger.error(f'Failsafe failed to unmute/undeafen {current_member_state.name}: {e}')
async def _soundboard_grace_protocol(member: discord.Member, config: BotConfig):
    try:
        if member.voice and (member.voice.mute or member.voice.deaf):
            await member.edit(mute=False, deafen=False)
    except Exception:
        pass
    await asyncio.sleep(2.0)
    guild = member.guild
    if not guild:
        return
    member_after_sleep = guild.get_member(member.id)
    moderated_vc_ids = {config.STREAMING_VC_ID, *config.ALT_VC_ID}
    is_in_moderated_vc = lambda ch: ch and ch.id in moderated_vc_ids
    if member_after_sleep and member_after_sleep.voice and member_after_sleep.voice.channel and is_in_moderated_vc(member_after_sleep.voice.channel) and (not member_after_sleep.voice.self_video):
        try:
            if state.vc_moderation_active:
                await member_after_sleep.edit(mute=True, deafen=True)
                logger.info(f'Re-applied mute/deafen for {member_after_sleep.name} after soundboard grace period.')
        except Exception as e:
            logger.error(f'Failed to re-mute {member_after_sleep.name} after grace period: {e}')
            
async def manage_menu_task_presence():
    """
    Manages starting/stopping background tasks based on user presence.
    Separates 'Menu/Report' tasks (stop immediately on empty) from
    'Security' tasks (stop after 39s grace period).
    """
    if not state.omegle_enabled:
        return 

    await asyncio.sleep(1.5)
    guild = bot.get_guild(bot_config.GUILD_ID)
    if not guild:
        return
    streaming_vc = guild.get_channel(bot_config.STREAMING_VC_ID)
    if not streaming_vc or not isinstance(streaming_vc, discord.VoiceChannel):
        return
    
    human_listeners_with_cam = [m for m in streaming_vc.members if not m.bot and m.id not in bot_config.ALLOWED_USERS and m.voice and m.voice.self_video]

    # --- STARTING LOGIC ---
    if human_listeners_with_cam or not bot_config.EMPTY_VC_PAUSE:
        log_reason = "Active user detected" if human_listeners_with_cam else "EMPTY_VC_PAUSE is False"

        # 1. Start Menu/Report Tasks
        if not periodic_menu_update.is_running():
            periodic_menu_update.start()
        if not periodic_times_report_update.is_running():
            periodic_times_report_update.start()
        if not periodic_timeouts_report_update.is_running():
            periodic_timeouts_report_update.start()

        # 2. Start Security Tasks
        if not capture_screenshots_task.is_running():
            logger.info(f'Starting screenshot capture task. ({log_reason})')
            capture_screenshots_task.start()
        if not check_ban_status_task.is_running():
            logger.info(f'Starting ban check task. ({log_reason})')
            check_ban_status_task.start()

        # 3. Cancel Grace Period if active (Users are back!)
        if state.empty_vc_grace_task and not state.empty_vc_grace_task.done():
            state.empty_vc_grace_task.cancel()

    # --- STOPPING LOGIC ---
    elif not human_listeners_with_cam and bot_config.EMPTY_VC_PAUSE:
        
        # 1. Stop Menu/Report Tasks IMMEDIATELY
        if periodic_menu_update.is_running():
            periodic_menu_update.stop()
        if periodic_times_report_update.is_running():
            periodic_times_report_update.stop()
        if periodic_timeouts_report_update.is_running():
            periodic_timeouts_report_update.stop()

        # 2. Handle Security Tasks (Ban/Screenshots)
        # Only stop them if the grace task is NOT running (meaning the 39s passed or wasn't scheduled)
        is_grace_running = state.empty_vc_grace_task and not state.empty_vc_grace_task.done()
        
        if not is_grace_running:
            if capture_screenshots_task.is_running():
                logger.info('VC is empty and no grace period active. Stopping screenshot task.')
                capture_screenshots_task.stop()
                async with state.screenshot_lock:
                    if hasattr(state, "ban_screenshots"):
                        state.ban_screenshots.clear()

            if check_ban_status_task.is_running():
                logger.info('VC is empty and no grace period active. Stopping ban check task.')
                check_ban_status_task.stop()
                
async def init_vc_moderation():
    async with state.vc_lock:
        is_active = state.vc_moderation_active
    if not is_active:
        logger.warning('VC Moderation is disabled on startup.')
        return
    guild = bot.get_guild(bot_config.GUILD_ID)
    if not guild:
        return
    streaming_vc = guild.get_channel(bot_config.STREAMING_VC_ID)
    if not streaming_vc:
        return
    async with state.vc_lock:
        for member in streaming_vc.members:
            if not member.bot and member.id not in state.active_vc_sessions:
                state.active_vc_sessions[member.id] = time.time()
                logger.info(f'Started tracking VC time for existing member: {member.name} (ID: {member.id})')
            if not member.bot and member.id not in bot_config.ALLOWED_USERS and (not (member.voice and member.voice.self_video)):
                try:
                    await asyncio.sleep(1)
                    if state.vc_moderation_active:
                        await member.edit(mute=True, deafen=True)
                        logger.info(f'Auto-muted/deafened {member.name} for camera off.')
                except Exception as e:
                    logger.error(f'Failed to auto mute/deafen {member.name}: {e}')
                state.camera_off_timers[member.id] = time.time()



def require_music_preconditions():
    async def predicate(ctx):
        # 1. Allow Bot Owners immediately
        if ctx.author.id in bot_config.ALLOWED_USERS:
            return True

        # 2. Check global disable list
        async with state.moderation_lock:
            if ctx.author.id in state.omegle_disabled_users:
                await ctx.send('You are currently disabled from using any commands.', delete_after=10)
                return False

        # 3. Check Command Channel
        if ctx.channel.id != bot_config.COMMAND_CHANNEL_ID:
            await ctx.send(f'All commands should be used in <#{bot_config.COMMAND_CHANNEL_ID}>.', delete_after=10)
            return False

        # 4. Check Camera Status (Existing Rule)
        if not is_user_in_streaming_vc_with_camera(ctx.author):
            await ctx.send('You must be in the Streaming VC with your camera on to use commands.', delete_after=10)
            return False

        # 5. NEW: Check Music Roles
        if bot_config.MUSIC_ROLES:
            user_roles = [role.name for role in ctx.author.roles]
            # Check if user has ANY of the allowed music roles
            if not any(role in user_roles for role in bot_config.MUSIC_ROLES):
                roles_str = ", ".join(bot_config.MUSIC_ROLES)
                await ctx.send(f'⛔ You need one of the following roles to control music: **{roles_str}**', delete_after=10)
                return False

        return True
    return commands.check(predicate)


@bot.event
async def on_ready() -> None:
    logger.info(f'Bot is online as {bot.user}')
    bot.state.is_interrupting_for_search = False
    try:
        log_channel_id = bot_config.LOG_GC
        if log_channel_id:
            channel = bot.get_channel(log_channel_id)
            if channel:
                await channel.send('✅ Bot is online and ready!')
            else:
                logger.error(f'LOG_GC channel (ID: {log_channel_id}) not found. Cannot send online message.')
        else:
            logger.info("LOG_GC not set in config. Skipping 'Bot is online' message in Discord.")
    except Exception as e:
        logger.error(f'Failed to send online message: {e}')
    try:
        await load_state_async()
        logger.info('State loaded successfully')
    except Exception as e:
        logger.error(f'Error loading state: {e}', exc_info=True)
    try:
        if not periodic_state_save.is_running():
            periodic_state_save.start()
        if not periodic_cleanup.is_running():
            periodic_cleanup.start()
        if not periodic_menu_update.is_running():
            periodic_menu_update.start()
        if not timeout_unauthorized_users_task.is_running():
            timeout_unauthorized_users_task.start()
        if not periodic_geometry_save.is_running():
            periodic_geometry_save.start()
        if not music_playback_watchdog.is_running():
            music_playback_watchdog.start()
        if not check_ban_status_task.is_running():
            check_ban_status_task.start()
        if not capture_screenshots_task.is_running():
            capture_screenshots_task.start()
        if not auto_delete_old_commands.is_running():
            auto_delete_old_commands.start()
            logger.info('Auto-delete command task started.')
        if not daily_auto_stats_clear.is_running():
            daily_auto_stats_clear.start()
            logger.info('Daily auto-stats task started.')
        if state.omegle_enabled:
            if await omegle_handler.initialize():
                pass
            else:
                logger.critical('Selenium initialization failed.')
        else:
            logger.warning('Omegle is disabled on startup. Skipping browser initialization.')
        if state.music_enabled:
            logger.info('Music is enabled on startup. Initializing music player...')
            guild = bot.get_guild(bot_config.GUILD_ID)
            if guild:
                streaming_vc = guild.get_channel(bot_config.STREAMING_VC_ID)
                if streaming_vc and any((m for m in streaming_vc.members if not m.bot and m.id not in bot_config.ALLOWED_USERS and m.voice.self_video)):
                    logger.info('Users detected in VC on startup, starting music playback.')
                else:
                    logger.info('No active users in VC on startup. Music will start when a user joins with camera on.')
        else:
            logger.info('Music is disabled by config on startup. Skipping music initialization.')
        asyncio.create_task(init_vc_moderation())
        async def register_hotkey(enabled_flag: bool, key_combo: str, callback_func: Callable, name: str):
            try:
                await asyncio.to_thread(keyboard.remove_hotkey, key_combo)
            except (KeyError, ValueError):
                pass
            if not enabled_flag:
                return
            def callback_wrapper():
                bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(callback_func()))
            try:
                await asyncio.to_thread(keyboard.add_hotkey, key_combo, callback_wrapper)
                logger.info(f'Registered global {name} hotkey: {key_combo}')
            except Exception as e:
                logger.error(f"Failed to register {name} hotkey '{key_combo}': {e}")
        await register_hotkey(bot_config.ENABLE_GLOBAL_HOTKEY, bot_config.GLOBAL_HOTKEY_COMBINATION, global_skip, 'skip')
        await register_hotkey(bot_config.ENABLE_GLOBAL_MSKIP, bot_config.GLOBAL_HOTKEY_MSKIP, global_mskip, 'mskip')
        await register_hotkey(bot_config.ENABLE_GLOBAL_MPAUSE, bot_config.GLOBAL_HOTKEY_MPAUSE, global_mpause, 'mpause')
        await register_hotkey(bot_config.ENABLE_GLOBAL_MVOLUP, bot_config.GLOBAL_HOTKEY_MVOLUP, global_mvolup, 'mvolup')
        await register_hotkey(bot_config.ENABLE_GLOBAL_MVOLDOWN, bot_config.GLOBAL_HOTKEY_MVOLDOWN, global_mvoldown, 'mvoldown')
        asyncio.create_task(manage_menu_task_presence())
        logger.info('Initialization complete')
        bot.is_fully_ready = True
    except Exception as e:
        logger.error(f'Error during on_ready: {e}', exc_info=True)
@bot.event
@handle_errors
async def on_member_join(member: discord.Member) -> None:
    await helper.handle_member_join(member)
@bot.event
@handle_errors
async def on_member_ban(guild: discord.Guild, user: discord.User) -> None:
    await helper.handle_member_ban(guild, user)
@bot.event
@handle_errors
async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    await helper.handle_member_unban(guild, user)
@bot.event
@handle_errors
async def on_member_remove(member: discord.Member) -> None:
    await helper.handle_member_remove(member)
async def manage_music_presence():
    if not state.music_enabled:
        return
    await asyncio.sleep(1.5)
    guild = bot.get_guild(bot_config.GUILD_ID)
    if not guild:
        logger.error("manage_music_presence: Guild not found.")
        return
    streaming_vc = guild.get_channel(bot_config.STREAMING_VC_ID)
    if not streaming_vc or not isinstance(streaming_vc, discord.VoiceChannel):
        logger.error("manage_music_presence: Streaming VC not found or invalid.")
        return
    human_listeners_with_cam = [m for m in streaming_vc.members if not m.bot and m.id not in bot_config.ALLOWED_USERS and m.voice and m.voice.self_video]
    is_bot_connected = bot.voice_client_music and bot.voice_client_music.is_connected()
    if is_bot_connected and (not human_listeners_with_cam):
        logger.info('No active users with cameras detected. Attempting to disconnect music bot.')
        async with state.music_lock:
            state.stop_after_clear = True
        try:
            await bot.voice_client_music.disconnect(force=True)
            logger.info('Successfully disconnected music bot.')
        except Exception as e:
            logger.error(f'Error during music bot disconnect (forcing cleanup): {e}', exc_info=True)
        finally:
            bot.voice_client_music = None
            async with state.music_lock:
                state.is_music_playing = False
                state.is_music_paused = False
                state.current_song = None
            await bot.change_presence(activity=None)
            asyncio.create_task(update_music_menu())
        return
    if not is_bot_connected and human_listeners_with_cam:
        # Bot is not connected, but users are here. Time to join.
        logger.info('Active user with camera detected and bot is not in VC. Triggering music start.')
        asyncio.create_task(start_music_playback())

async def _graceful_security_shutdown():
    """
    Waits 14 seconds, then stops the Ban Check and Screenshot tasks.
    This allows the bot to keep monitoring for a short time after the VC empties.
    """
    try:
        logger.info("Empty VC detected. Starting 39s grace period before stopping security tasks...")
        await asyncio.sleep(14)
        
        logger.info("Grace period ended. Stopping Ban Check and Screenshot tasks.")
        if check_ban_status_task.is_running():
            check_ban_status_task.stop()
        
        if capture_screenshots_task.is_running():
            capture_screenshots_task.stop()
            # Clear screenshot buffer to save memory
            async with state.screenshot_lock:
                if hasattr(state, "ban_screenshots"):
                    state.ban_screenshots.clear()
                    logger.info("Cleared in-memory screenshot buffer.")
                    
    except asyncio.CancelledError:
        logger.info("Graceful security shutdown CANCELLED. Users returned to VC.")
    finally:
        # Clean up the task reference in state
        if state.empty_vc_grace_task:
            state.empty_vc_grace_task = None

@bot.event
@handle_errors
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    if not getattr(bot, 'is_fully_ready', False):
        return
    if member.id == bot.user.id or member.bot:
        return

    # --- VC Definitions ---
    moderated_vc_ids = {bot_config.STREAMING_VC_ID, *bot_config.ALT_VC_ID}
    is_in_moderated_vc = lambda ch: ch and ch.id in moderated_vc_ids
    was_in_mod_vc = is_in_moderated_vc(before.channel)
    is_now_in_mod_vc = is_in_moderated_vc(after.channel)
    was_in_streaming_vc = before.channel and before.channel.id == bot_config.STREAMING_VC_ID
    is_now_in_streaming_vc = after.channel and after.channel.id == bot_config.STREAMING_VC_ID

    # --- Time Tracking Logic ---
    async with state.vc_lock:
        if is_now_in_streaming_vc and (not was_in_streaming_vc):
            if member.id not in state.active_vc_sessions:
                state.active_vc_sessions[member.id] = time.time()
                if member.id not in state.vc_time_data:
                    state.vc_time_data[member.id] = {'total_time': 0, 'sessions': [], 'username': member.name, 'display_name': member.display_name}
                logger.info(f"VC Time Tracking: '{member.display_name}' started session.")
        elif was_in_streaming_vc and (not is_now_in_streaming_vc):
            if member.id in state.active_vc_sessions:
                start_time = state.active_vc_sessions.pop(member.id)
                duration = time.time() - start_time
                if member.id in state.vc_time_data:
                    state.vc_time_data[member.id]['total_time'] += duration
                    state.vc_time_data[member.id]['sessions'].append({'start': start_time, 'end': time.time(), 'duration': duration, 'vc_name': before.channel.name})
                    logger.info(f"VC Time Tracking: '{member.display_name}' ended session, adding {duration:.1f}s.")

    # --- Moderation Logic ---
    is_mod_active = state.vc_moderation_active
    if is_mod_active:
        if is_now_in_mod_vc and (not was_in_mod_vc):
            # User Joined a Moderated VC
            if is_now_in_streaming_vc:
                logger.info(f'VC JOIN: {member.display_name} ({member.name} | ID: {member.id}).')
                asyncio.create_task(_handle_stream_vc_join(member))
                if member.id not in bot_config.ALLOWED_USERS:
                    asyncio.create_task(_join_camera_failsafe_check(member, bot_config))
            
            if member.id not in bot_config.ALLOWED_USERS:
                async with state.vc_lock:
                    state.camera_off_timers[member.id] = time.time()
                    logger.info(f"Started camera grace period timer for '{member.display_name}'.")
                asyncio.create_task(_soundboard_grace_protocol(member, bot_config))
        
        elif was_in_mod_vc and (not is_now_in_mod_vc):
            # User Left a Moderated VC
            if was_in_streaming_vc:
                logger.info(f'VC LEAVE: {member.display_name} ({member.name} | ID: {member.id}).')

        elif was_in_mod_vc and is_now_in_mod_vc:
            # User Switched / Changed State in Moderated VC
            if before.channel.id == bot_config.STREAMING_VC_ID and after.channel.id != bot_config.STREAMING_VC_ID:
                logger.info(f'VC SWITCH: {member.display_name} ({member.name} | ID: {member.id}).')
            
            camera_turned_on = not before.self_video and after.self_video
            camera_turned_off = before.self_video and (not after.self_video)
            
            if member.id not in bot_config.ALLOWED_USERS:
                if camera_turned_off:
                    async with state.vc_lock:
                        state.camera_off_timers[member.id] = time.time()
                    try:
                        if state.vc_moderation_active:
                            await member.edit(mute=True, deafen=True)
                            logger.info(f"Auto-muted/deafened '{member.display_name}' for turning camera off.")
                    except Exception as e:
                        logger.error(f"Failed to auto-mute '{member.display_name}': {e}")
                elif camera_turned_on:
                    async with state.vc_lock:
                        state.camera_off_timers.pop(member.id, None)
                    if not state.hush_override_active and state.vc_moderation_active:
                        try:
                            await member.edit(mute=False, deafen=False)
                            logger.info(f"Auto-unmuted '{member.display_name}' after turning camera on.")
                        except Exception as e:
                            logger.error(f"Failed to auto-unmute '{member.display_name}': {e}")

    # --- Omegle Automation Logic ---
    is_relevant_event = was_in_streaming_vc or is_now_in_streaming_vc
    if is_relevant_event and state.omegle_enabled and (not state.is_banned):
        # Calculate active camera users
        if is_now_in_streaming_vc:
            cam_users_after_count = len([m for m in after.channel.members if m.voice and m.voice.self_video and (not m.bot) and (m.id not in bot_config.ALLOWED_USERS)])
        elif was_in_streaming_vc and (not is_now_in_streaming_vc):
            cam_users_after_count = len([m for m in before.channel.members if m.voice and m.voice.self_video and (not m.bot) and (m.id not in bot_config.ALLOWED_USERS)])
        else:
            cam_users_after_count = 0
        
        camera_turned_on = is_now_in_streaming_vc and (not before.self_video) and after.self_video
        camera_turned_off = was_in_streaming_vc and before.self_video and (not after.self_video)
        joined_with_cam = not was_in_streaming_vc and is_now_in_streaming_vc and after.self_video
        left_with_cam = was_in_streaming_vc and (not is_now_in_streaming_vc) and before.self_video
        
        cam_users_before_count = cam_users_after_count
        if member.id not in bot_config.ALLOWED_USERS:
            if camera_turned_on or joined_with_cam:
                cam_users_before_count -= 1
            elif camera_turned_off or left_with_cam:
                cam_users_before_count += 1
        cam_users_before_count = max(0, cam_users_before_count)

        # 1. Auto Start (Skip) Logic
        if bot_config.AUTO_VC_START and cam_users_before_count == 0 and (cam_users_after_count > 0):
            logger.info(f'Auto Skip: Camera users went from 0 to {cam_users_after_count}. Triggering skip command.')
            await omegle_handler.custom_skip()
            if (command_channel := member.guild.get_channel(bot_config.COMMAND_CHANNEL_ID)):
                try:
                    await command_channel.send('Stream automatically started')
                except Exception as e:
                    logger.error(f'Failed to send auto-skip notification: {e}')

        # 2. Auto Stop (Refresh) Logic
        if bot_config.EMPTY_VC_PAUSE and cam_users_before_count > 0 and (cam_users_after_count == 0):
            # Pause the Stream (!refresh) if the bot was "live" (relay sent)
            is_bot_live = False
            if state:
                async with state.moderation_lock:
                    is_bot_live = state.relay_command_sent
            
            if is_bot_live:
                logger.info(f'Auto Refresh: Last camera user left. Bot is live, sending pause command.')
                await omegle_handler.refresh()
                if (command_channel := member.guild.get_channel(bot_config.COMMAND_CHANNEL_ID)):
                    try:
                        await command_channel.send('Stream automatically paused')
                    except Exception as e:
                        logger.error(f'Failed to send auto-refresh notification: {e}')
            
            # Start the 39s Grace Period for Security Tasks
            # Cancel existing task if somehow running to reset timer
            if state.empty_vc_grace_task and not state.empty_vc_grace_task.done():
                state.empty_vc_grace_task.cancel()
            
            # _graceful_security_shutdown must be defined in bot.py
            state.empty_vc_grace_task = asyncio.create_task(_graceful_security_shutdown())

    # --- Punishment VC Cleanup ---
    if after.channel and after.channel.id == bot_config.PUNISHMENT_VC_ID:
        if member.voice and (member.voice.mute or member.voice.deaf):
            try:
                await member.edit(mute=False, deafen=False)
                logger.info(f"Automatically unmuted/undeafened '{member.display_name}' in Punishment VC.")
            except Exception as e:
                logger.error(f"Failed to unmute/undeafen '{member.display_name}' in Punishment VC: {e}")

    # --- Trigger Presence Checks ---
    is_event_in_streaming_vc = before.channel and before.channel.id == bot_config.STREAMING_VC_ID or (after.channel and after.channel.id == bot_config.STREAMING_VC_ID)
    if is_event_in_streaming_vc:
        asyncio.create_task(manage_music_presence())
        asyncio.create_task(manage_menu_task_presence())

@bot.event
@handle_errors
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    # --- 1. Handle Role Changes ---
    if before.roles != after.roles:
        roles_gained = [role for role in after.roles if role not in before.roles and role.name != '@everyone']
        roles_lost = [role for role in before.roles if role not in after.roles and role.name != '@everyone']
        if roles_gained or roles_lost:
            async with state.moderation_lock:
                state.recent_role_changes.append((after.id, after.name, [r.name for r in roles_gained], [r.name for r in roles_lost], datetime.now(timezone.utc)))
            channel = after.guild.get_channel(bot_config.CHAT_CHANNEL_ID)
            if channel:
                embed = await build_role_update_embed(after, roles_gained, roles_lost)
                await channel.send(embed=embed)
                
    # --- 2. Handle Timeout Changes ---
    if before.is_timed_out() != after.is_timed_out():
        if after.is_timed_out():
            # --- TIMEOUT ADDED ---
            # This 'if' block runs when a timeout is ADDED
            async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id and hasattr(entry.after, 'timed_out_until') and (entry.after.timed_out_until is not None):
                    duration = (entry.after.timed_out_until - datetime.now(timezone.utc)).total_seconds()
                    reason = entry.reason or 'No reason provided'
                    moderator = entry.user
                    await helper.send_timeout_notification(after, moderator, int(duration), reason)
                    await helper._log_timeout_in_state(after, int(duration), reason, moderator.name, moderator.id)
                    asyncio.create_task(helper.update_timeouts_report_menu()) # Assumes helper.update_timeouts_report_menu() exists
                    break
        else:
            # --- TIMEOUT REMOVED ---
            # This 'else' block runs when a timeout is REMOVED
            async with state.moderation_lock:
                if after.id in state.pending_timeout_removals:
                    return  # Already processing this removal
                state.pending_timeout_removals[after.id] = True
            
            try:
                moderator_name = 'System'
                moderator_id = None
                reason = 'Timeout Expired Naturally'

                try:
                    # INCREASED WAIT: Give Discord API more time to write the log
                    await asyncio.sleep(3.5) 
                    
                    # IMPROVED FETCH: 
                    # 1. Increased limit to 20 to catch it in busy servers.
                    # 2. Removed 'after=' param to prevent clock skew issues.
                    # 3. Added a manual check for 'entry.created_at' to ensure we don't grab old logs.
                    async for entry in after.guild.audit_logs(limit=20, action=discord.AuditLogAction.member_update):
                        
                        # Check if this log is for the user we are checking
                        if entry.target.id == after.id:
                            
                            # Check if this log is specifically for REMOVING a timeout
                            # (before had a date, after is None)
                            if getattr(entry.before, 'timed_out_until', None) is not None and getattr(entry.after, 'timed_out_until', None) is None:
                                
                                # Check if the log happened recently (within the last 30 seconds)
                                # This ensures we don't accidentally grab an old manual removal
                                time_diff = datetime.now(timezone.utc) - entry.created_at
                                if time_diff.total_seconds() < 30:
                                    moderator_name = entry.user.name
                                    moderator_id = entry.user.id
                                    reason = f'Manually removed by 🛡️ {moderator_name}'
                                    break
                except discord.Forbidden:
                    logger.warning('Cannot check audit logs for un-timeout (Missing Permissions).')
                except Exception as e:
                    logger.error(f'Error checking audit logs for un-timeout: {e}')
                
                # Now update the state
                async with state.moderation_lock:
                    start_timestamp = state.active_timeouts.get(after.id, {}).get('start_timestamp', time.time())
                    duration = int(time.time() - start_timestamp)
                    state.recent_untimeouts.append((after.id, after.name, after.display_name, datetime.now(timezone.utc), reason, moderator_name, moderator_id))
                    if len(state.recent_untimeouts) > 100:
                        state.recent_untimeouts.pop(0)
                    state.active_timeouts.pop(after.id, None)
                
                # Try to send the notification, but don't stop if it fails
                try:
                    await helper.send_timeout_removal_notification(after, duration, reason)
                except Exception as e:
                    logger.error(f"Failed to send timeout removal notification for {after.name} (this is normal for natural expirations): {e}", exc_info=True)
                
                # This line will now be reached every time
                asyncio.create_task(helper.update_timeouts_report_menu())
            
            finally:
                # Always remove the lock at the end
                async with state.moderation_lock:
                    state.pending_timeout_removals.pop(after.id, None)

@bot.event
@handle_errors
async def on_message(message: discord.Message) -> None:
    if message.author.bot or not message.guild or message.guild.id != bot_config.GUILD_ID:
        return
    if bot_config.MEDIA_ONLY_CHANNEL_ID and message.channel.id == bot_config.MEDIA_ONLY_CHANNEL_ID:
        if bot_config.MOD_MEDIA:
            if message.author.id not in bot_config.ALLOWED_USERS:
                is_media_present = False
                if message.attachments:
                    is_media_present = True
                if not is_media_present and message.embeds:
                    for embed in message.embeds:
                        if embed.type in ['image', 'gifv', 'video']:
                            is_media_present = True
                            break
                if not is_media_present:
                    try:
                        await message.delete()
                        logger.info(f'Deleted message from {message.author} in media-only channel #{message.channel.name} because it contained no media.')
                        await message.channel.send(f'{message.author.mention}, this channel only allows photos and other media.', delete_after=10)
                    except discord.Forbidden:
                        logger.warning(f'Missing permissions to delete message in media-only channel #{message.channel.name}.')
                    except discord.NotFound:
                        pass
                    except Exception as e:
                        logger.error(f'Error deleting message in media-only channel: {e}')
                    return
    await bot.process_commands(message)
async def _trigger_full_menu_repost():
    if state.menu_repost_lock.locked():
        logger.info('Menu repost lock is held, skipping redundant repost trigger.')
        return
    async with state.menu_repost_lock:
        logger.warning('Triggering a full menu repost due to a missing menu...')
        try:
            guild = bot.get_guild(bot_config.GUILD_ID)
            if not guild:
                return
            channel = guild.get_channel(bot_config.COMMAND_CHANNEL_ID)
            if not channel:
                logger.error(f'Cannot repost menus: Command channel ID {bot_config.COMMAND_CHANNEL_ID} not found.')
                return
            state.music_menu_message_id = None
            state.times_report_message_id = None
            state.timeouts_report_message_id = None # Assumes state.timeouts_report_message_id exists
            await safe_purge(channel, limit=100)
            await asyncio.sleep(1)
            times_report_msg = await helper.show_times_report(channel)
            if times_report_msg and hasattr(state, 'times_report_message_id'):
                state.times_report_message_id = times_report_msg.id
            await asyncio.sleep(1)
            timeouts_report_msg = await helper.show_timeouts_report(channel) 
            if timeouts_report_msg and hasattr(state, 'timeouts_report_message_id'):
                state.timeouts_report_message_id = timeouts_report_msg.id
            await asyncio.sleep(1)
            if state.music_enabled:
                music_menu_msg = await helper.send_music_menu(channel)
                if music_menu_msg:
                    state.music_menu_message_id = music_menu_msg.id
                await asyncio.sleep(1)
            if state.omegle_enabled:
                await helper.send_help_menu(channel)
            logger.info('Successfully completed triggered full menu repost.')
        except Exception as e:
            logger.error(f'Error during triggered menu repost: {e}', exc_info=True)
@tasks.loop(minutes=9.31)
async def periodic_menu_update() -> None:
    if state.menu_repost_lock.locked():
        logger.info('Periodic menu update skipped: Repost lock is currently held.')
        return
    async with state.menu_repost_lock:
        logger.info('Starting scheduled periodic menu update...')
        try:
            guild = bot.get_guild(bot_config.GUILD_ID)
            if not guild:
                return
            channel = guild.get_channel(bot_config.COMMAND_CHANNEL_ID)
            if not channel:
                logger.warning(f'Help menu channel with ID {bot_config.COMMAND_CHANNEL_ID} not found.')
                return
            state.music_menu_message_id = None
            state.times_report_message_id = None
            state.timeouts_report_message_id = None # Assumes state.timeouts_report_message_id exists
            await safe_purge(channel, limit=100)
            await asyncio.sleep(1)
            times_report_msg = await helper.show_times_report(channel)
            if times_report_msg:
                state.times_report_message_id = times_report_msg.id
            await asyncio.sleep(1)
            timeouts_report_msg = await helper.show_timeouts_report(channel) 
            if timeouts_report_msg:
                state.timeouts_report_message_id = timeouts_report_msg.id
            await asyncio.sleep(1)
            if state.music_enabled:
                music_menu_msg = await helper.send_music_menu(channel)
                if music_menu_msg and hasattr(state, 'music_menu_message_id'):
                    state.music_menu_message_id = music_menu_msg.id
                await asyncio.sleep(1)
            if state.omegle_enabled:
                await helper.send_help_menu(channel)
            logger.info('Scheduled periodic menu update completed.')
        except Exception as e:
            logger.error(f'Periodic menu update task failed: {e}', exc_info=True)
async def safe_purge(channel: Any, limit: int=100) -> None:
    if not hasattr(channel, 'purge'):
        logger.warning(f"Attempted to purge channel '{getattr(channel, 'name', 'Unknown')}' which is not a messageable channel.")
        return
    two_weeks_ago = datetime.now(timezone.utc) - timedelta(days=14)
    try:
        deleted = await channel.purge(limit=limit, check=lambda m: m.created_at > two_weeks_ago)
        if deleted:
            logger.info(f'Purged {len(deleted)} messages in {channel.name}')
            await asyncio.sleep(1)
    except discord.HTTPException as e:
        if e.status == 429:
            wait = max(e.retry_after, 10)
            logger.warning(f'Purge rate limited in {channel.name}. Waiting {wait}s')
            await asyncio.sleep(wait)
        else:
            logger.error(f'An HTTP error occurred during purge: {e}', exc_info=True)
    except discord.Forbidden:
        logger.warning(f'Missing permissions to purge messages in {channel.name}.')
    except Exception as e:
        logger.error(f'An unexpected error occurred during purge: {e}', exc_info=True)
@tasks.loop(time=dt_time(bot_config.AUTO_STATS_HOUR_UTC, bot_config.AUTO_STATS_MINUTE_UTC))
async def daily_auto_stats_clear() -> None:
    stats_channel_id = bot_config.AUTO_STATS_CHAN or bot_config.CHAT_CHANNEL_ID
    channel = bot.get_channel(stats_channel_id)
    if not channel:
        logger.error(f'Daily stats channel with ID {stats_channel_id} not found! Cannot run daily stats clear.')
        return
    report_sent_successfully = False
    try:
        await helper.show_analytics_report(channel)
        report_sent_successfully = True
    except Exception as e:
        logger.error(f"Daily auto-stats failed during 'show_analytics_report': {e}", exc_info=True)
        try:
            await channel.send('⚠️ **Critical Error:** Failed to generate the daily stats report. **Statistics will NOT be cleared.** Please check the logs.')
        except Exception as e_inner:
            logger.error(f'Failed to send the critical error message to the channel: {e_inner}')
    if report_sent_successfully:
        try:
            streaming_vc = channel.guild.get_channel(bot_config.STREAMING_VC_ID)
            current_members = []
            if streaming_vc:
                current_members.extend([m for m in streaming_vc.members if not m.bot])
            for vc_id in bot_config.ALT_VC_ID:
                if (alt_vc := channel.guild.get_channel(vc_id)):
                    current_members.extend([m for m in alt_vc.members if not m.bot])
            async with state.vc_lock, state.analytics_lock, state.moderation_lock:
                state.vc_time_data = {}
                state.active_vc_sessions = {}
                state.analytics = {'command_usage': {}, 'command_usage_by_user': {}, 'violation_events': 0}
                state.user_violations = {}
                state.camera_off_timers = {}
                if current_members:
                    current_time = time.time()
                    for member in current_members:
                        state.active_vc_sessions[member.id] = current_time
                        state.vc_time_data[member.id] = {'total_time': 0, 'sessions': [], 'username': member.name, 'display_name': member.display_name}
                    logger.info(f'Restarted VC tracking for {len(current_members)} members after auto-clear')
            await channel.send('✅ Statistics automatically cleared and tracking restarted!')
        except Exception as e:
            logger.error(f'Daily auto-stats failed during state clearing: {e}', exc_info=True)
@tasks.loop(seconds=15)
@handle_errors
async def timeout_unauthorized_users_task() -> None:
    async with state.vc_lock:
        is_active = state.vc_moderation_active
    if not is_active:
        return
    guild = bot.get_guild(bot_config.GUILD_ID)
    if not guild:
        return
    punishment_vc = guild.get_channel(bot_config.PUNISHMENT_VC_ID)
    if not punishment_vc:
        logger.warning('Punishment VC not found, moderation task cannot run.')
        return
    moderated_vcs = []
    if (streaming_vc := guild.get_channel(bot_config.STREAMING_VC_ID)):
        moderated_vcs.append(streaming_vc)
    for vc_id in bot_config.ALT_VC_ID:
        if (alt_vc := guild.get_channel(vc_id)):
            if alt_vc not in moderated_vcs:
                moderated_vcs.append(alt_vc)
    if not moderated_vcs:
        logger.warning('No valid moderated VCs found.')
        return
    users_to_check = []
    current_time = time.time()
    async with state.vc_lock:
        for member_id, start_time in list(state.camera_off_timers.items()):
            if current_time - start_time >= bot_config.CAMERA_OFF_ALLOWED_TIME:
                users_to_check.append(member_id)
    for member_id in users_to_check:
        member = guild.get_member(member_id)
        if not member or not member.voice or (not member.voice.channel):
            async with state.vc_lock:
                state.camera_off_timers.pop(member_id, None)
            continue
        vc = member.voice.channel
        async with state.vc_lock:
            timer_start_time = state.camera_off_timers.get(member_id)
            if not timer_start_time or time.time() - timer_start_time < bot_config.CAMERA_OFF_ALLOWED_TIME:
                continue
            state.camera_off_timers.pop(member_id, None)
        violation_count = 0
        async with state.moderation_lock:
            state.analytics['violation_events'] += 1
            state.user_violations[member_id] = state.user_violations.get(member_id, 0) + 1
            violation_count = state.user_violations[member_id]
        try:
            punishment_applied = ''
            if violation_count == 1:
                reason = f'Must have camera on while in the {vc.name} VC.'
                await member.move_to(punishment_vc, reason=reason)
                punishment_applied = 'moved'
                await helper.send_punishment_vc_notification(member, reason, bot.user.mention)
                logger.info(f'Moved {member.name} to PUNISHMENT VC (from {vc.name}).')
            elif violation_count == 2:
                timeout_duration = bot_config.TIMEOUT_DURATION_SECOND_VIOLATION
                reason = f'2nd camera violation in {vc.name}.'
                await member.timeout(timedelta(seconds=timeout_duration), reason=reason)
                punishment_applied = 'timed out'
                await helper._log_timeout_in_state(member, timeout_duration, reason, 'AutoMod')
                asyncio.create_task(helper.update_timeouts_report_menu()) # Assumes helper.update_timeouts_report_menu() exists
                logger.info(f'Timed out {member.name} for {timeout_duration}s (from {vc.name}).')
            else:
                timeout_duration = bot_config.TIMEOUT_DURATION_THIRD_VIOLATION
                reason = f'Repeated camera violations in {vc.name}.'
                await member.timeout(timedelta(seconds=timeout_duration), reason=reason)
                punishment_applied = 'timed out'
                await helper._log_timeout_in_state(member, timeout_duration, reason, 'AutoMod')
                asyncio.create_task(helper.update_timeouts_report_menu()) # Assumes helper.update_timeouts_report_menu() exists
                logger.info(f'Timed out {member.name} for {timeout_duration}s (from {vc.name}).')
            is_dm_disabled = False
            async with state.moderation_lock:
                is_dm_disabled = member_id in state.users_with_dms_disabled
            if not is_dm_disabled:
                try:
                    await member.send(f"You've been {punishment_applied} for not having a camera on in the VC.")
                except discord.Forbidden:
                    async with state.moderation_lock:
                        state.users_with_dms_disabled.add(member_id)
                except Exception as e:
                    logger.error(f'Failed to send violation DM to {member.name}: {e}')
        except discord.Forbidden:
            logger.warning(f'Missing permissions to punish {member.name} in {vc.name}.')
        except discord.HTTPException as e:
            logger.error(f'Failed to punish {member.name} in {vc.name}: {e}')
        except Exception as e:
            logger.error(f'An unexpected error occurred during punishment for {member.name}: {e}')
@tasks.loop(seconds=33)
async def music_playback_watchdog():
    if not state.music_enabled:
        return
    guild = bot.get_guild(bot_config.GUILD_ID)
    if not guild:
        return
    streaming_vc = guild.get_channel(bot_config.STREAMING_VC_ID)
    if not streaming_vc:
        return
    human_listeners_with_cam = [m for m in streaming_vc.members if not m.bot and m.id not in bot_config.ALLOWED_USERS and m.voice and m.voice.self_video]
    is_bot_connected = bot.voice_client_music and bot.voice_client_music.is_connected()
    if human_listeners_with_cam and (not is_bot_connected) or (not human_listeners_with_cam and is_bot_connected):
        logger.info('Watchdog: Mismatch in bot presence and listeners. Triggering presence manager.')
        asyncio.create_task(manage_music_presence())
        return
    async with state.music_lock:
        is_processing = state.is_processing_song
    if human_listeners_with_cam and is_bot_connected:
        if not bot.voice_client_music.is_playing() and (not bot.voice_client_music.is_paused()) and (not is_processing):
            # FIX: Check if we actually have content to play to avoid infinite loop
            async with state.music_lock:
                has_content = bool(state.all_songs or state.shuffle_queue or state.active_playlist or state.search_queue)
            
            if has_content:
                logger.warning('Watchdog: Bot is connected but idle with listeners present. Force-starting playback.')
                await start_music_playback()
@music_playback_watchdog.before_loop
async def before_music_watchdog():
    await bot.wait_until_ready()
async def announce_command_usage(ctx: commands.Context, command_name: str):
    try:
        if ctx.channel.id == bot_config.COMMAND_CHANNEL_ID:
            announcement_content = f'**{ctx.author.display_name}** used `{command_name}`'
            await ctx.channel.send(announcement_content, delete_after=30.0)
            logger.info(f'Announced command use: {ctx.author.name} used {command_name}')
    except discord.Forbidden:
        logger.warning(f'Missing permissions to send announcement message in #{ctx.channel.name}')
    except Exception as e:
        logger.error(f'Failed to send command usage announcement: {e}')
@bot.command(name='help')
@require_allowed_user()
@handle_errors
async def help_command(ctx):
    await helper.send_help_menu(ctx)
@bot.command(name='skip', aliases=['start'])
@require_user_preconditions()
@omegle_command_cooldown
@handle_errors
async def skip(ctx):
    if not state.omegle_enabled:
        await ctx.send('Omegle features are currently disabled. Use `!enableomegle` to start.', delete_after=10)
        return
    command_name = f'!{ctx.invoked_with}'
    if not getattr(ctx, 'from_button', False):
        record_command_usage(state.analytics, command_name)
        record_command_usage_by_user(state.analytics, ctx.author.id, command_name)
        await announce_command_usage(ctx, command_name)
        await omegle_handler.custom_skip(ctx)
@bot.command(name='refresh', aliases=['pause'])
@require_user_preconditions()
@omegle_command_cooldown
@handle_errors
async def refresh(ctx):
    if not state.omegle_enabled:
        await ctx.send('Omegle features are currently disabled. Use `!enableomegle` to start.', delete_after=10)
        return
    command_name = f'!{ctx.invoked_with}'
    if not getattr(ctx, 'from_button', False):
        record_command_usage(state.analytics, command_name)
        record_command_usage_by_user(state.analytics, ctx.author.id, command_name)
        await announce_command_usage(ctx, command_name)
        await omegle_handler.refresh(ctx)
@bot.command(name='report')
@require_admin_preconditions()
@omegle_command_cooldown
@handle_errors
async def report(ctx):
    if not state.omegle_enabled:
        await ctx.send('Omegle features are currently disabled.', delete_after=10)
        return
    command_name = f'!{ctx.invoked_with}'
    if not getattr(ctx, 'from_button', False):
        record_command_usage(state.analytics, command_name)
        record_command_usage_by_user(state.analytics, ctx.author.id, command_name)
        await announce_command_usage(ctx, command_name)
        await omegle_handler.report_user(ctx)
@bot.command(name='purge')
@require_allowed_user()
@handle_errors
async def purge(ctx, count: int) -> None:
    logger.info(f'Purge command received with count: {count}')
    deleted = await ctx.channel.purge(limit=count + 1)
    logger.info(f'Purged {len(deleted)} messages.')
@purge.error
async def purge_error(ctx, error: Exception) -> None:
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send('Usage: !purge <number>')
    elif isinstance(error, commands.CheckFailure):
        await ctx.send('⛔ You do not have permission to use this command.')
    else:
        await ctx.send('An error occurred in the purge command.')
        logger.error(f'Error in purge command: {error}', exc_info=True)
@bot.command(name='shutdown')
@require_allowed_user()
@handle_errors
async def shutdown(ctx) -> None:
    if getattr(bot, '_is_shutting_down', False):
        await ctx.send('🛑 Shutdown already in progress.')
        return
    confirm_msg = await ctx.send('⚠️ **Are you sure you want to shut down the bot?**\nReact with ✅ to confirm or ❌ to cancel.')
    for emoji in ('✅', '❌'):
        await confirm_msg.add_reaction(emoji)
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in {'✅', '❌'} and (reaction.message.id == confirm_msg.id)
    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=30.0, check=check)
        if str(reaction.emoji) == '❌':
            await confirm_msg.edit(content='🟢 Shutdown cancelled.')
            return
    except asyncio.TimeoutError:
        await confirm_msg.edit(content='🟢 Shutdown timed out.')
        return
    finally:
        try:
            await confirm_msg.clear_reactions()
        except discord.HTTPException:
            pass
    await _initiate_shutdown(ctx)
@bot.command(name='disableomegle')
@require_allowed_user()
@handle_errors
async def disable_omegle(ctx):
    if not state.omegle_enabled:
        await ctx.send('❌ Omegle features are already disabled.', delete_after=10)
        return
    state.omegle_enabled = False
    await omegle_handler.close()
    asyncio.create_task(save_state_async())
    logger.warning(f'Omegle features DISABLED by {ctx.author.name}')
    await ctx.send('✅ Omegle features have been **DISABLED**. The browser is closed and the Omegle help menu will no longer be posted.')
@bot.command(name='enableomegle')
@require_allowed_user()
@handle_errors
async def enable_omegle(ctx):
    if state.omegle_enabled:
        await ctx.send('✅ Omegle features are already enabled.', delete_after=10)
        return
    state.omegle_enabled = True
    await ctx.send('⏳ Enabling Omegle features. Launching browser...')
    if not await omegle_handler.initialize():
        await ctx.send('❌ **Critical Error:** Failed to launch the browser. Please check the logs.')
        state.omegle_enabled = False
        return
    asyncio.create_task(save_state_async())
    logger.warning(f'Omegle features ENABLED by {ctx.author.name}')
    await ctx.send('✅ Omegle features have been **ENABLED**. The browser is running and the Omegle help menu will now be posted periodically.')
@bot.command(name='music')
@require_allowed_user()
@handle_errors
async def music_command(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    await helper.send_music_menu(ctx)
async def is_song_in_queue(state: BotState, song_path_or_url: str) -> bool:
    async with state.music_lock:
        if state.current_song and state.current_song.get('path') == song_path_or_url:
            return True
        all_queued_paths = {song.get('path') for song in state.active_playlist}
        all_queued_paths.update({song.get('path') for song in state.search_queue})
        if song_path_or_url in all_queued_paths:
            return True
    return False
@bot.command(name='mpauseplay', aliases=['mpp'])
@require_music_preconditions()
@handle_errors
async def mpauseplay(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    if not getattr(ctx, 'from_button', False):
        await announce_command_usage(ctx, f'!{ctx.invoked_with}')
    if not await ensure_voice_connection():
        await ctx.send('❌ Music player is not connected and could not reconnect.', delete_after=10)
        return
    was_stopped = False
    async with state.music_lock:
        if bot.voice_client_music.is_playing():
            bot.voice_client_music.pause()
            state.is_music_paused = True
            state.is_music_playing = False
            logger.info('Music paused.')
        elif bot.voice_client_music.is_paused():
            bot.voice_client_music.resume()
            state.is_music_paused = False
            state.is_music_playing = True
            logger.info('Music resumed.')
        else:
            was_stopped = True
    if was_stopped:
        logger.info('Music started via toggle command.')
        await play_next_song()
    else:
        asyncio.create_task(update_music_menu())
@bot.command(name='mskip')
@require_music_preconditions()
@handle_errors
async def mskip(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    if not getattr(ctx, 'from_button', False):
        await announce_command_usage(ctx, f'!{ctx.invoked_with}')
    if not await ensure_voice_connection():
        await ctx.send('❌ Music player is not connected and could not reconnect.', delete_after=10)
        return
    if not bot.voice_client_music or not (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused()):
        await ctx.send('Nothing is currently playing to skip.', delete_after=10)
        return
    old_song_title = 'the current song'
    async with state.music_lock:
        if state.current_song:
            old_song_title = state.current_song.get('title', 'Unknown Title')
        if state.music_mode == 'loop':
            state.music_mode = 'shuffle'
            await ctx.send('🔁 Loop mode disabled. Switching to 🔀 Shuffle mode.', delete_after=10)
            logger.info(f'Loop mode disabled by {ctx.author.name} via skip. Switched to Shuffle.')
        state.is_music_paused = False
        state.announcement_context = ctx
    bot.voice_client_music.stop()
    logger.info(f"Song '{old_song_title}' skipped by {ctx.author.name}. Awaiting next song announcement.")
@bot.command(name='volume', aliases=['vol'])
@require_user_preconditions()
@handle_errors
async def volume(ctx, level: int):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    if not await ensure_voice_connection():
        await ctx.send('❌ Music player is not connected and could not reconnect.', delete_after=10)
        return
    if not 0 <= level <= 100:
        await ctx.send(f'Volume must be between 0 and 100.', delete_after=10)
        return
    async with state.music_lock:
        new_volume = round(level / 100 * bot_config.MUSIC_MAX_VOLUME, 2)
        state.music_volume = new_volume
        if bot.voice_client_music.source:
            bot.voice_client_music.source.volume = new_volume
    await ctx.send(f'Volume set to {level}%', delete_after=5)
    logger.info(f'Volume set to {level}% ({state.music_volume}) by {ctx.author.name}')
    asyncio.create_task(update_music_menu())
def extract_youtube_url(query: str) -> Optional[str]:
    pattern = re.compile('(?:https?://)?(?:www\\.)?(?:m\\.)?(?:music\\.)?(?:youtube\\.com|youtu\\.be)/(?:watch\\?v=|embed/|v/|shorts/)?([\\w-]{11})')
    match = pattern.search(query)
    if match:
        video_id = match.group(1)
        return f'https://www.youtube.com/watch?v={video_id}'
    return None
@bot.command(name='msearch', aliases=['m'])
@require_music_preconditions()
@handle_errors
async def msearch(ctx, *, query: str):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    if not await ensure_voice_connection():
        await ctx.send('❌ Music player is not connected and could not reconnect.', delete_after=10)
        return
    search_query = query.strip()
    status_msg = await ctx.send(f'⏳ Searching for `{search_query}`...')
    clean_query = extract_youtube_url(search_query) or search_query
    all_hits = []
    is_youtube_search = False
    url_pattern = re.compile('https?://(www\\.)?((music\\.)?youtube|youtu|soundcloud|spotify|bandcamp)\\.(com|be)/.+')
    is_spotify_url = 'spotify' in clean_query.lower()
    is_generic_url = url_pattern.match(clean_query)

    if is_spotify_url:
        if not sp:
            await status_msg.edit(content='❌ Spotify support is not configured. Missing credentials in `.env` file.')
            return
        await status_msg.edit(content=f'Spotify link detected. Fetching metadata from Spotify API...')
        try:
            tracks_to_search = []
            
            # --- NEW: Define a max limit for playlists to prevent abuse ---
            MAX_PLAYLIST_TRACKS = 100

            if '/track/' in clean_query:
                # FIX: Async wrapper
                track_info = await asyncio.to_thread(sp.track, clean_query)
                if track_info:
                    tracks_to_search.append(track_info)
            elif '/album/' in clean_query:
                # FIX: Async wrapper
                results = await asyncio.to_thread(sp.album_tracks, clean_query)
                if results:
                    tracks_to_search.extend(results['items'])
            elif '/playlist/' in clean_query:
                # --- MODIFIED: Fetch in pages of 50 ---
                # FIX: Async wrapper
                results = await asyncio.to_thread(sp.playlist_tracks, clean_query, limit=50)
                if results:
                    while results:
                        # Add the tracks from this page
                        new_tracks = [item['track'] for item in results['items'] if item and item.get('track')]
                        tracks_to_search.extend(new_tracks)
                        
                        # --- MODIFIED: Check limit and stop if full or no more pages ---
                        if len(tracks_to_search) >= MAX_PLAYLIST_TRACKS:
                            logger.warning(f'Spotify playlist processing limit ({MAX_PLAYLIST_TRACKS}) reached. Truncating list.')
                            tracks_to_search = tracks_to_search[:MAX_PLAYLIST_TRACKS]
                            results = None # Stop the loop
                        elif results['next']:
                            logger.debug(f"Fetching next page of Spotify playlist... (current count: {len(tracks_to_search)})")
                            # FIX: Async wrapper
                            results = await asyncio.to_thread(sp.next, results) # Get next page
                        else:
                            results = None # No more pages

            if not tracks_to_search:
                raise ValueError('Could not retrieve any tracks from the Spotify URL.')
            
            youtube_queries = [f"{track['artists'][0]['name']} {track['name']}" for track in tracks_to_search if track and track.get('name') and track.get('artists')]
            
            if not youtube_queries:
                raise ValueError('Could not extract any song titles from the Spotify link.')
            
            await status_msg.edit(content=f'⏳ Found {len(youtube_queries)} track(s). Searching on YouTube...')
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                for yt_query in youtube_queries:
                    try:
                        search_results = await asyncio.to_thread(ydl.extract_info, f'ytsearch1:{yt_query}', download=False)
                        if search_results and search_results.get('entries'):
                            video_info = search_results['entries'][0]
                            title = video_info.get('title', '').lower()
                            if '[deleted video]' in title or '[private video]' in title:
                                logger.info(f"Skipping unavailable Spotify->YouTube result: {video_info.get('title')}")
                                continue
                            all_hits.append({'title': video_info.get('title', 'Unknown Title'), 'path': video_info.get('webpage_url', video_info.get('url')), 'is_stream': True, 'ctx': ctx})
                    except Exception:
                        logger.warning(f"Could not find a YouTube match for Spotify query '{yt_query}'")
        
        # --- NEW: Catch Spotify API errors (like the 404 you saw) ---
        except spotipy.exceptions.SpotifyException as e:
            logger.error(f"A Spotify API error occurred: {e}", exc_info=True)
            await status_msg.edit(content=f'❌ A Spotify API error occurred. The playlist might be private or invalid (404).')
            return
        except Exception as e:
            logger.error(f"An error occurred while processing the Spotify link: {e}", exc_info=True)
            await status_msg.edit(content=f'❌ An error occurred while processing the Spotify link: {e}')
            return
        
        if not all_hits:
            await status_msg.edit(content=f'❌ Could not find any YouTube matches for the tracks in the Spotify link.')
            return
        
        added_count, skipped_count, was_idle = (0, 0, False)
        async with state.music_lock:
            existing_paths = {s.get('path') for s in state.active_playlist + state.search_queue}
            if state.current_song:
                existing_paths.add(state.current_song.get('path'))
            new_songs_to_queue = []
            for song in all_hits:
                song_path = song.get('path')
                if song_path and song_path not in existing_paths:
                    new_songs_to_queue.append(song)
                    existing_paths.add(song_path)
                else:
                    skipped_count += 1
            if new_songs_to_queue:
                state.search_queue.extend(new_songs_to_queue)
                added_count = len(new_songs_to_queue)
                was_idle = not (bot.voice_client_music and (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused()))
        
        response_msg = f'✅ Added **{added_count}** songs to the queue from the Spotify link.'
        if ('/playlist/' in clean_query or '/album/' in clean_query) and len(youtube_queries) == MAX_PLAYLIST_TRACKS:
            response_msg = f'✅ Added **{added_count}** songs, hitting the {MAX_PLAYLIST_TRACKS} track limit for playlists.'
        
        if skipped_count > 0:
            response_msg += f' ({skipped_count} duplicates were skipped).'
        await status_msg.edit(content=response_msg)
        
        asyncio.create_task(update_music_menu())
        if was_idle and added_count > 0:
            await play_next_song()
        return

    elif is_generic_url:
        await status_msg.edit(content=f'⏳ Processing URL: `{clean_query}`...')
        try:
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                search_results = await asyncio.to_thread(ydl.extract_info, clean_query, download=False)
                if search_results and 'entries' in search_results:
                    for entry in search_results['entries']:
                        if not entry or not entry.get('url'):
                            continue
                        title = entry.get('title', '').lower()
                        if '[deleted video]' in title or '[private video]' in title:
                            logger.info(f"Skipping unavailable video from URL/Playlist: {entry.get('title')}")
                            continue
                        all_hits.append({'title': entry.get('title', 'Unknown Title'), 'path': entry.get('webpage_url', entry.get('url')), 'is_stream': True, 'ctx': ctx})
                elif search_results and search_results.get('url'):
                    title = search_results.get('title', '').lower()
                    if '[deleted video]' not in title and '[private video]' not in title:
                        all_hits.append({'title': search_results.get('title', 'Unknown Title'), 'path': search_results.get('webpage_url', search_results.get('url')), 'is_stream': True, 'ctx': ctx})
                    else:
                        logger.info(f"Skipping unavailable video from single URL: {search_results.get('title')}")
        except Exception as e:
            logger.warning(f"Direct URL processing for '{clean_query}' failed with error: {e}. Falling back to text search.")
    if not all_hits:
        if not is_generic_url:
            await status_msg.edit(content=f'⏳ Searching for `{clean_query}` in the local library...')
            search_terms = [re.sub('[^a-z0-9]', '', term) for term in clean_query.lower().split()]
            local_hits = []
            if search_terms:
                for song_path, metadata in MUSIC_METADATA_CACHE.items():
                    searchable_metadata = re.sub('[^a-z0-9]', '', os.path.basename(song_path).lower()) + metadata.get('artist', '') + metadata.get('title', '') + metadata.get('album', '')
                    if all((term in searchable_metadata for term in search_terms)):
                        display_title = get_display_title_from_path(song_path)
                        local_hits.append({'title': display_title, 'path': song_path, 'is_stream': False, 'ctx': ctx})
            all_hits.extend(local_hits)
        if not all_hits:
            await status_msg.edit(content=f'⏳ No local results. Searching YouTube for `{clean_query}`...')
            is_youtube_search = True
            try:
                with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                    search_results = await asyncio.to_thread(ydl.extract_info, f'ytsearch10:{clean_query}', download=False)
                    if search_results and 'entries' in search_results:
                        for entry in search_results['entries']:
                            if entry and entry.get('url'):
                                title = entry.get('title', '').lower()
                                if '[deleted video]' in title or '[private video]' in title:
                                    logger.info(f"Skipping unavailable video from search: {entry.get('title')}")
                                    continue
                                all_hits.append({'title': entry.get('title', 'Unknown Title'), 'path': entry.get('webpage_url', entry.get('url')), 'is_stream': True, 'ctx': ctx})
            except Exception as e:
                await status_msg.edit(content=f'❌ An error occurred while searching YouTube: {e}')
                logger.error(f"Youtube search failed for query '{clean_query}': {e}")
                return
    if not all_hits:
        await status_msg.edit(content=f'❌ No songs found matching `{search_query}`.')
        return
    if is_generic_url and len(all_hits) > 1:
        added_count, skipped_count, was_idle = (0, 0, False)
        async with state.music_lock:
            existing_paths = {s.get('path') for s in state.active_playlist + state.search_queue}
            if state.current_song:
                existing_paths.add(state.current_song.get('path'))
            new_songs_to_queue = []
            for song in all_hits:
                if song.get('path') and song['path'] not in existing_paths:
                    new_songs_to_queue.append(song)
                    existing_paths.add(song['path'])
                else:
                    skipped_count += 1
            if new_songs_to_queue:
                state.search_queue.extend(new_songs_to_queue)
                added_count = len(new_songs_to_queue)
                was_idle = not (bot.voice_client_music and (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused()))
        response_msg = f'✅ Added **{added_count}** songs to the queue from the playlist.'
        if skipped_count > 0:
            response_msg += f' ({skipped_count} duplicates were skipped).'
        await ctx.send(response_msg)
        asyncio.create_task(update_music_menu())
        try:
            await status_msg.delete()
        except discord.NotFound:
            pass
        if was_idle and added_count > 0:
            await play_next_song()
        return
    if is_generic_url and len(all_hits) == 1:
        song_to_add = all_hits[0]
        song_title = song_to_add.get('title', 'Unknown Title')
        if await is_song_in_queue(state, song_to_add['path']):
            await status_msg.edit(content=f'⚠️ **{song_title}** is already in the queue.')
            return
        was_idle = False
        async with state.music_lock:
            state.search_queue.append(song_to_add)
            was_idle = not (bot.voice_client_music and (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused()))
        await status_msg.edit(content=f'✅ Added **{song_title}** to the queue.')
        asyncio.create_task(update_music_menu())
        if was_idle:
            await play_next_song()
        return
    class SearchResultsView(discord.ui.View):
        def __init__(self, hits: list, author: discord.Member, query: str, is_Youtube: bool, youtube_page: int=1):
            super().__init__(timeout=180.0)
            self.hits, self.author, self.query, self.is_Youtube, self.youtube_page = (hits, author, query, is_Youtube, youtube_page)
            self.current_page, self.page_size = (0, 23)
            self.total_pages = (len(self.hits) + self.page_size - 1) // self.page_size
            self.total_pages = max(1, self.total_pages)
            self.message = None
            self.update_components()
        def update_components(self):
            self.clear_items()
            self.add_item(self.create_dropdown())
            if not self.is_Youtube and self.total_pages > 1:
                self.add_item(self.create_nav_button('⬅️ Prev', 'prev_page', self.current_page == 0))
                self.add_item(self.create_nav_button('Next ➡️', 'next_page', self.current_page >= self.total_pages - 1))
            if self.is_Youtube:
                self.add_item(self.create_youtube_nav_button('Next Page ➡️', 'youtube_next_page', len(self.hits) < 10))
        def create_dropdown(self) -> discord.ui.Select:
            start_index = self.current_page * self.page_size
            end_index = start_index + self.page_size
            page_hits = self.hits[start_index:end_index]
            options = []
            if not self.is_Youtube:
                options.append(discord.SelectOption(label=f"Search YouTube for '{self.query[:50]}'", value='search_youtube', emoji='📺'))
            if page_hits:
                options.append(discord.SelectOption(label=f'Add All ({len(page_hits)}) On This Page', value='add_all', emoji='➕'))
            for i, hit in enumerate(page_hits):
                options.append(discord.SelectOption(label=f"{start_index + i + 1}. {hit['title']}"[:95], value=str(start_index + i)))
            placeholder = f'Page {self.current_page + 1}/{self.total_pages}...' if not self.is_Youtube else f'YouTube Page {self.youtube_page}...'
            select_menu = discord.ui.Select(placeholder=placeholder, options=options)
            select_menu.callback = self.select_callback
            return select_menu
        def create_nav_button(self, label: str, custom_id: str, disabled: bool) -> discord.ui.Button:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=custom_id, disabled=disabled)
            async def nav_callback(interaction: discord.Interaction):
                if interaction.user != self.author:
                    await interaction.response.send_message('You cannot control this menu.', ephemeral=True)
                    return
                if interaction.data['custom_id'] == 'prev_page':
                    self.current_page -= 1
                elif interaction.data['custom_id'] == 'next_page':
                    self.current_page += 1
                self.update_components()
                await interaction.response.edit_message(view=self)
            button.callback = nav_callback
            return button
        def create_youtube_nav_button(self, label: str, custom_id: str, disabled: bool) -> discord.ui.Button:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id, disabled=disabled)
            async def youtube_nav_callback(interaction: discord.Interaction):
                if interaction.user != self.author:
                    await interaction.response.send_message('You cannot control this menu.', ephemeral=True)
                    return
                await interaction.response.edit_message(content=f'⏳ Loading page {self.youtube_page + 1} of YouTube results...', view=None)
                next_page = self.youtube_page + 1
                next_page_ydl_opts = YDL_OPTIONS.copy()
                next_page_ydl_opts['playliststart'] = self.youtube_page * 10 + 1
                new_hits = []
                try:
                    with yt_dlp.YoutubeDL(next_page_ydl_opts) as ydl:
                        search_results = await asyncio.to_thread(ydl.extract_info, f'ytsearch10:{self.query}', download=False)
                        if 'entries' in search_results:
                            for entry in search_results.get('entries', []):
                                if not entry or not entry.get('url'):
                                    continue
                                title = entry.get('title', '').lower()
                                if '[deleted video]' in title or '[private video]' in title:
                                    logger.info(f"Skipping unavailable video from YouTube 'Next Page': {entry.get('title')}")
                                    continue
                                new_hits.append({'title': entry.get('title', 'Unknown Title'), 'path': entry.get('webpage_url', entry.get('url')), 'is_stream': True})
                except Exception as e:
                    logger.error(f"YouTube next page search failed for query '{self.query}': {e}", exc_info=True)
                    self.update_components()
                    await interaction.message.edit(content='An error occurred.', view=self)
                    return
                if not new_hits:
                    self.disabled = True
                    self.update_components()
                    await interaction.message.edit(content='No more results found.', view=self)
                    return
                new_view = SearchResultsView(hits=new_hits, author=self.author, query=self.query, is_Youtube=True, youtube_page=next_page)
                new_view.message = interaction.message
                await interaction.message.edit(content=f'Showing YouTube results page {next_page}:', view=new_view)
            button.callback = youtube_nav_callback
            return button
        async def select_callback(self, interaction: discord.Interaction):
            await interaction.response.defer()
            if interaction.user != self.author:
                await interaction.followup.send('You cannot control this menu.', ephemeral=True)
                return
            selected_value = interaction.data['values'][0]
            if selected_value == 'search_youtube':
                await interaction.message.edit(content=f'⏳ Searching YouTube for `{self.query}`...', view=None)
                youtube_hits = []
                try:
                    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                        search_results = await asyncio.to_thread(ydl.extract_info, f'ytsearch10:{self.query}', download=False)
                        if 'entries' in search_results:
                            for entry in search_results['entries']:
                                if not entry or not entry.get('url'):
                                    continue
                                title = entry.get('title', '').lower()
                                if '[deleted video]' in title or '[private video]' in title:
                                    logger.info(f"Skipping unavailable video from 'Search YouTube' button: {entry.get('title')}")
                                    continue
                                youtube_hits.append({'title': entry.get('title', 'Unknown Title'), 'path': entry.get('webpage_url', entry.get('url')), 'is_stream': True})
                except Exception as e:
                    await interaction.message.edit(content=f'❌ An error occurred: {e}')
                    logger.error(f'Youtube failed: {e}')
                    return
                if not youtube_hits:
                    await interaction.message.edit(content=f'❌ No songs found on YouTube for `{self.query}`.')
                    return
                new_view = SearchResultsView(youtube_hits, self.author, self.query, is_Youtube=True, youtube_page=1)
                new_view.message = interaction.message
                await interaction.message.edit(content=f'Found {len(youtube_hits)} results from YouTube:', view=new_view)
                return
            if selected_value == 'add_all':
                start_index, end_index = (self.current_page * self.page_size, (self.current_page + 1) * self.page_size)
                songs_to_add_raw = self.hits[start_index:end_index]
                songs_to_add, already_in_queue_count = ([], 0)
                async with state.music_lock:
                    existing_paths = {s.get('path') for s in state.active_playlist + state.search_queue}
                    if state.current_song:
                        existing_paths.add(state.current_song.get('path'))
                for song in songs_to_add_raw:
                    if song.get('path') and song['path'] not in existing_paths:
                        songs_to_add.append(song)
                        existing_paths.add(song['path'])
                    else:
                        already_in_queue_count += 1
                if not songs_to_add:
                    await interaction.followup.send(f'✅ All songs on this page are already in the queue.', ephemeral=True)
                    return
                async with state.music_lock:
                    state.search_queue.extend(songs_to_add)
                    was_idle = not (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused())
                response_msg = f'🎵 {interaction.user.mention} added {len(songs_to_add)} songs.'
                if already_in_queue_count > 0:
                    response_msg += f' ({already_in_queue_count} were duplicates).'
                await interaction.followup.send(response_msg)
                asyncio.create_task(update_music_menu())
                if was_idle:
                    await asyncio.create_task(play_next_song())
                if self.current_page < self.total_pages - 1:
                    self.current_page += 1
                    self.update_components()
                    await interaction.message.edit(view=self)
                else:
                    for item in self.children:
                        item.disabled = True
                        await interaction.message.edit(content='Added songs from the last page.', view=self)
            else:
                selected_song = self.hits[int(selected_value)]
                if await is_song_in_queue(bot.state, selected_song['path']):
                    await interaction.followup.send(f"⚠️ **{selected_song['title']}** is already in the queue.", ephemeral=True)
                    return
                async with state.music_lock:
                    state.search_queue.append(selected_song)
                    was_idle = not (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused())
                await interaction.followup.send(f"🎵 {interaction.user.mention} added **{selected_song['title']}** to the queue.")
                asyncio.create_task(update_music_menu())
                if was_idle:
                    await play_next_song()
        async def on_timeout(self):
            if self.message:
                for item in self.children:
                    item.disabled = True
                try:
                    await self.message.edit(content='Search menu timed out.', view=self)
                except discord.NotFound:
                    pass
    view = SearchResultsView(all_hits, ctx.author, query=search_query, is_Youtube=is_youtube_search)
    content_msg = f'Found {len(all_hits)} results. Select a song to add:'
    view.message = await status_msg.edit(content=content_msg, view=view)
@bot.command(name='mclear')
@require_music_preconditions()
@handle_errors
async def mclear(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    if not getattr(ctx, 'from_button', False):
        await announce_command_usage(ctx, f'!{ctx.invoked_with}')
        await helper.confirm_and_clear_music_queue(ctx)
@bot.command(name='mshuffle')
@require_allowed_user()
@handle_errors
async def mshuffle(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    if not getattr(ctx, 'from_button', False):
        await announce_command_usage(ctx, f'!{ctx.invoked_with}')
    modes_cycle = ['shuffle', 'alphabetical', 'loop']
    display_map = {'shuffle': ('Shuffle', '🔀'), 'alphabetical': ('Alphabetical', '▶️'), 'loop': ('Loop', '🔁')}
    async with state.music_lock:
        try:
            current_index = modes_cycle.index(state.music_mode)
        except ValueError:
            current_index = -1
        new_mode = modes_cycle[(current_index + 1) % len(modes_cycle)]
        state.music_mode = new_mode
        display_name, emoji = display_map[new_mode]
    await ctx.send(f'{emoji} Music mode is now **{display_name}**.')
    logger.info(f'Music mode set to {new_mode} by {ctx.author.name}')
    asyncio.create_task(update_music_menu())
@bot.command(name='nowplaying', aliases=['np'])
@require_music_preconditions()
@handle_errors
async def nowplaying(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    record_command_usage(state.analytics, '!nowplaying')
    record_command_usage_by_user(state.analytics, ctx.author.id, '!nowplaying')
    await helper.show_now_playing(ctx)
@bot.command(name='queue', aliases=['q'])
@require_music_preconditions()
@handle_errors
async def queue(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    command_name = f'!{ctx.invoked_with}'
    record_command_usage(state.analytics, command_name)
    record_command_usage_by_user(state.analytics, ctx.author.id, command_name)
    await helper.show_queue(ctx)
@bot.group(name='playlist', invoke_without_command=True)
@require_music_preconditions()
@handle_errors
async def playlist(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are currently disabled. Use `!mon` to enable.', delete_after=10)
        return
    record_command_usage(state.analytics, '!playlist')
    record_command_usage_by_user(state.analytics, ctx.author.id, '!playlist')
    await ctx.send('Invalid playlist command. Use `!playlist save|load|list|delete <name>`.', delete_after=10)
@playlist.command(name='save')
@handle_errors
async def playlist_save(ctx, *, name: str):
    async with state.music_lock:
        queue_to_save = state.active_playlist + state.search_queue
        if not queue_to_save:
            await ctx.send('The queue is empty, there is nothing to save.', delete_after=10)
            return
        state.playlists[name.lower()] = list(queue_to_save)
    await ctx.send(f'✅ Playlist **{name}** saved with {len(queue_to_save)} songs.')
    await save_state_async()
@playlist.command(name='load')
@handle_errors
async def playlist_load(ctx, *, name: Optional[str]=None):
    if not name:
        await ctx.send('Usage: `!playlist load <playlist_name>`', delete_after=10)
        return
    playlist_name, added_count, skipped_count, was_idle = (name.lower(), 0, 0, False)
    async with state.music_lock:
        if playlist_name not in state.playlists:
            await ctx.send(f'❌ Playlist **{name}** could not be found.', delete_after=10)
            return
        songs_to_load = state.playlists[playlist_name]
        existing_paths = {s.get('path') for s in state.active_playlist + state.search_queue}
        if state.current_song:
            existing_paths.add(state.current_song.get('path'))
        new_songs_to_queue = []
        for song in songs_to_load:
            if (song_path := song.get('path')):
                if song_path not in existing_paths:
                    new_songs_to_queue.append(song)
                    existing_paths.add(song_path)
                    added_count += 1
                else:
                    skipped_count += 1
        if new_songs_to_queue:
            state.search_queue.extend(new_songs_to_queue)
            was_idle = not (bot.voice_client_music and (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused()))
    response_msg = f'✅ Playlist **{name}** loaded. Added {added_count} new songs.'
    if skipped_count > 0:
        response_msg += f' Skipped {skipped_count} duplicate(s).'
    await ctx.send(response_msg)
    asyncio.create_task(update_music_menu())
    if was_idle and added_count > 0:
        await play_next_song()
@playlist.command(name='list')
@handle_errors
async def playlist_list(ctx):
    async with state.music_lock:
        if not state.playlists:
            await ctx.send('There are no saved playlists.', delete_after=10)
            return
        embed = discord.Embed(title='💾 Saved Playlists', color=discord.Color.green())
        desc_parts = [f'• **{p_name.capitalize()}**: {len(songs)} songs' for p_name, songs in state.playlists.items()]
        embed.description = '\n'.join(desc_parts)
    await ctx.send(embed=embed)
@playlist.command(name='delete')
@handle_errors
async def playlist_delete(ctx, *, name: str):
    playlist_name = name.lower()
    async with state.music_lock:
        if playlist_name not in state.playlists:
            await ctx.send(f'❌ Playlist **{name}** could not be found.', delete_after=10)
            return
        del state.playlists[playlist_name]
    await ctx.send(f'✅ Playlist **{name}** has been deleted.')
    await save_state_async()
async def _initiate_shutdown(ctx: Optional[commands.Context]=None):
    if getattr(bot, '_is_shutting_down', False):
        return
    bot._is_shutting_down = True
    author_name = ctx.author.name if ctx else 'the system'
    logger.critical(f"Shutdown initiated by {author_name} (ID: {(ctx.author.id if ctx else 'N/A')})")
    if ctx:
        await ctx.send('🛑 **Bot is shutting down...**')
    async def unregister_hotkey(enabled, combo, name):
        try:
            await asyncio.to_thread(keyboard.remove_hotkey, combo)
        except Exception:
            pass
    await unregister_hotkey(bot_config.ENABLE_GLOBAL_HOTKEY, bot_config.GLOBAL_HOTKEY_COMBINATION, 'skip')
    await unregister_hotkey(bot_config.ENABLE_GLOBAL_MSKIP, bot_config.GLOBAL_HOTKEY_MSKIP, 'mskip')
    await unregister_hotkey(bot_config.ENABLE_GLOBAL_MPAUSE, bot_config.GLOBAL_HOTKEY_MPAUSE, 'mpause')
    await unregister_hotkey(bot_config.ENABLE_GLOBAL_MVOLUP, bot_config.GLOBAL_HOTKEY_MVOLUP, 'mvolup')
    await unregister_hotkey(bot_config.ENABLE_GLOBAL_MVOLDOWN, bot_config.GLOBAL_HOTKEY_MVOLDOWN, 'mvoldown')
    if bot.voice_client_music and bot.voice_client_music.is_connected():
        await bot.voice_client_music.disconnect()
    await bot.close()
@bot.command(name='hush')
@require_allowed_user()
@handle_errors
async def hush(ctx) -> None:
    async with state.vc_lock:
        state.hush_override_active = True
    streaming_vc = ctx.guild.get_channel(bot_config.STREAMING_VC_ID)
    if streaming_vc:
        impacted = []
        for member in streaming_vc.members:
            if not member.bot and member.id not in bot_config.ALLOWED_USERS:
                try:
                    await member.edit(mute=True)
                    impacted.append(member.name)
                except Exception as e:
                    logger.error(f'Error muting {member.name}: {e}')
        await ctx.send('Muted: ' + ', '.join(impacted) if impacted else 'No users muted.')
    else:
        await ctx.send('Streaming VC not found.')
@bot.command(name='secret')
@require_allowed_user()
@handle_errors
async def secret(ctx) -> None:
    async with state.vc_lock:
        state.hush_override_active = True
    streaming_vc = ctx.guild.get_channel(bot_config.STREAMING_VC_ID)
    if streaming_vc:
        impacted = []
        for member in streaming_vc.members:
            if not member.bot and member.id not in bot_config.ALLOWED_USERS:
                try:
                    await member.edit(mute=True, deafen=True)
                    impacted.append(member.name)
                except Exception as e:
                    logger.error(f'Error muting/deafening {member.name}: {e}')
        await ctx.send('Muted & Deafened: ' + ', '.join(impacted) if impacted else 'No users to mute/deafen.')
    else:
        await ctx.send('Streaming VC not found.')
@bot.command(name='rhush', aliases=['removehush'])
@require_allowed_user()
@handle_errors
async def rhush(ctx) -> None:
    async with state.vc_lock:
        state.hush_override_active = False
    streaming_vc = ctx.guild.get_channel(bot_config.STREAMING_VC_ID)
    if streaming_vc:
        impacted = []
        for member in streaming_vc.members:
            if not member.bot and (is_user_in_streaming_vc_with_camera(member) or member.id in bot_config.ALLOWED_USERS):
                try:
                    await member.edit(mute=False)
                    impacted.append(member.name)
                except Exception as e:
                    logger.error(f'Error unmuting {member.name}: {e}')
        await ctx.send('Unmuted: ' + ', '.join(impacted) if impacted else 'No users to unmute.')
    else:
        await ctx.send('Streaming VC not found.')
@bot.command(name='rsecret', aliases=['removesecret'])
@require_allowed_user()
@handle_errors
async def rsecret(ctx) -> None:
    async with state.vc_lock:
        state.hush_override_active = False
    streaming_vc = ctx.guild.get_channel(bot_config.STREAMING_VC_ID)
    if streaming_vc:
        impacted = []
        for member in streaming_vc.members:
            if not member.bot and (is_user_in_streaming_vc_with_camera(member) or member.id in bot_config.ALLOWED_USERS):
                try:
                    await member.edit(mute=False, deafen=False)
                    impacted.append(member.name)
                except Exception as e:
                    logger.error(f'Error removing mute/deafen from {member.name}: {e}')
        await ctx.send('Unmuted & Undeafened: ' + ', '.join(impacted) if impacted else 'No users to unmute/undeafen.')
    else:
        await ctx.send('Streaming VC not found.')
@bot.command(name='modoff')
@require_allowed_user()
@handle_errors
async def modoff(ctx):
    async with state.vc_lock:
        state.vc_moderation_active = False
    logger.warning(f'VC Moderation DISABLED by {ctx.author.name}')
    await ctx.send('🛡️ VC Moderation has been temporarily **DISABLED**.')
    asyncio.create_task(save_state_async())
@bot.command(name='modon')
@require_allowed_user()
@handle_errors
async def modon(ctx):
    async with state.vc_lock:
        state.vc_moderation_active = True
    logger.warning(f'VC Moderation ENABLED by {ctx.author.name}')
    await ctx.send('🛡️ VC Moderation has been **ENABLED**.')
    asyncio.create_task(save_state_async())
@bot.command(name='disablenotifications')
@require_allowed_user()
@handle_errors
async def disablenotifications(ctx):
    async with state.moderation_lock:
        if not state.notifications_enabled:
            await ctx.send('❌ Notifications are already disabled.', delete_after=10)
            return
        state.notifications_enabled = False
    await ctx.send('✅ Notifications for unbans, leaves, kicks, and timeout removals have been **DISABLED**.')
    logger.info(f'Notifications DISABLED by {ctx.author.name}')
    asyncio.create_task(save_state_async())
@bot.command(name='enablenotifications')
@require_allowed_user()
@handle_errors
async def enablenotifications(ctx):
    async with state.moderation_lock:
        if state.notifications_enabled:
            await ctx.send('✅ Notifications are already enabled.', delete_after=10)
            return
        state.notifications_enabled = True
    await ctx.send('✅ Notifications for unbans, leaves, kicks, and timeout removals have been **ENABLED**.')
    logger.info(f'Notifications ENABLED by {ctx.author.name}')
    asyncio.create_task(save_state_async())
@bot.command(name='moff')
@require_music_preconditions()
@handle_errors
async def moff(ctx):
    if not state.music_enabled:
        await ctx.send('Music features are already disabled.', delete_after=10)
        return
    if hasattr(state, 'music_menu_message_id') and state.music_menu_message_id:
        try:
            old_message_id = state.music_menu_message_id
            channel = bot.get_channel(bot_config.COMMAND_CHANNEL_ID)
            if channel:
                message_to_delete = await channel.fetch_message(old_message_id)
                await message_to_delete.delete()
                logger.info(f'Deleted old music menu message (ID: {old_message_id}).')
            else:
                logger.warning(f'Could not find command channel (ID: {bot_config.COMMAND_CHANNEL_ID}) to delete music menu.')
        except discord.NotFound:
            logger.info('Tried to delete music menu, but it was already gone.')
        except discord.Forbidden:
            logger.error(f'Missing permissions to delete music menu in channel (ID: {bot_config.COMMAND_CHANNEL_ID}).')
        except Exception as e:
            logger.error(f'An unexpected error occurred while deleting the music menu: {e}', exc_info=True)
        finally:
            state.music_menu_message_id = None
    logger.warning(f'Music features DISABLED by {ctx.author.name}')
    state.music_enabled = False
    async with state.music_lock:
        state.search_queue.clear()
        state.active_playlist.clear()
        state.current_song = None
        state.is_music_playing = False
        state.is_music_paused = False
        state.stop_after_clear = True
        if bot.voice_client_music and (bot.voice_client_music.is_playing() or bot.voice_client_music.is_paused()):
            bot.voice_client_music.stop()
    if bot.voice_client_music and bot.voice_client_music.is_connected():
        await bot.voice_client_music.disconnect(force=True)
        bot.voice_client_music = None
    await bot.change_presence(activity=None)
    await ctx.send('❌ Music features have been **DISABLED** and the player has been disconnected.')
    asyncio.create_task(save_state_async())
@bot.command(name='mon')
@require_music_preconditions()
@handle_errors
async def mon(ctx):
    if state.music_enabled:
        await ctx.send('Music features are already enabled. Triggering a refresh of the command menus...', delete_after=10)
        logger.info('!mon run while already enabled. Triggering full menu refresh task.')
        asyncio.create_task(periodic_menu_update())
        return
    logger.warning(f'Music features ENABLED by {ctx.author.name}')
    state.music_enabled = True
    await ctx.send('✅ Music features have been **ENABLED**. Connecting to voice and refreshing menus...')
    await start_music_playback()
    logger.info('!mon triggering full periodic_menu_update task.')
    asyncio.create_task(periodic_menu_update())
    asyncio.create_task(save_state_async())
@bot.command(name='disable')
@require_allowed_user()
@handle_errors
async def disable(ctx, user: discord.User):
    if not user:
        await ctx.send('Could not find that user.')
        return
    if user.id in bot_config.ALLOWED_USERS:
        await ctx.send('Cannot disable Allowed Users.')
        return
    async with state.moderation_lock:
        if user.id in state.omegle_disabled_users:
            await ctx.send(f'User {user.mention} is already disabled.')
            return
        state.omegle_disabled_users.add(user.id)
    await ctx.send(f'✅ User {user.mention} has been **disabled** from using any commands.')
    logger.info(f'User {user.name} disabled from all commands by {ctx.author.name}.')
    asyncio.create_task(helper.update_timeouts_report_menu()) # Assumes helper.update_timeouts_report_menu() exists
    asyncio.create_task(save_state_async())
@bot.command(name='enable')
@require_allowed_user()
@handle_errors
async def enable(ctx, user: discord.User):
    if not user:
        await ctx.send('Could not find that user.')
        return
    async with state.moderation_lock:
        if user.id not in state.omegle_disabled_users:
            await ctx.send(f'User {user.mention} is not disabled.')
            return
        state.omegle_disabled_users.remove(user.id)
    await ctx.send(f'✅ User {user.mention} has been **re-enabled** and can use commands again.')
    logger.info(f'User {user.name} re-enabled for all commands by {ctx.author.name}.')
    asyncio.create_task(helper.update_timeouts_report_menu()) # Assumes helper.update_timeouts_report_menu() exists
    asyncio.create_task(save_state_async())
@bot.command(name='ban')
@require_allowed_user()
@handle_errors
async def ban(ctx, *, user_input_str: str):
    potential_users = user_input_str.split()
    if not potential_users:
        await ctx.send('Usage: `!ban <@user or user_id> [@user2 or user_id2] ...`')
        return
    users_to_ban = []
    failed_to_find = []
    for p_user in potential_users:
        user_id = None
        match = re.match('<@!?(\\d+)>$', p_user)
        if match:
            user_id = match.group(1)
        elif p_user.isdigit():
            user_id = p_user
        if user_id:
            try:
                user_to_ban = await bot.fetch_user(int(user_id))
                users_to_ban.append(user_to_ban)
            except discord.NotFound:
                failed_to_find.append(f'`{p_user}` (User not found)')
            except Exception as e:
                failed_to_find.append(f'`{p_user}` (Error: {e})')
        else:
            failed_to_find.append(f'`{p_user}` (Invalid ID or mention format)')
    if not users_to_ban:
        await ctx.send('Could not find any valid users to ban.\n' + '\n'.join(failed_to_find))
        return
    user_list_str = '\n'.join([f'- **{user.name}** (`{user.id}`)' for user in users_to_ban])
    confirm_msg_content = f'⚠️ **Are you sure you want to ban the following user(s)?**\n{user_list_str}\n\nReact with ✅ to confirm or ❌ to cancel.'
    confirm_msg = await ctx.send(confirm_msg_content)
    for emoji in ('✅', '❌'):
        await confirm_msg.add_reaction(emoji)
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in {'✅', '❌'} and (reaction.message.id == confirm_msg.id)
    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=60.0, check=check)
        if str(reaction.emoji) == '❌':
            await confirm_msg.edit(content='🟢 Ban command cancelled.', view=None)
            return
    except asyncio.TimeoutError:
        await confirm_msg.edit(content='⌛ Ban command timed out.', view=None)
        return
    finally:
        try:
            await confirm_msg.clear_reactions()
        except discord.HTTPException:
            pass
    try:
        await confirm_msg.edit(content='📝 **Please provide a reason for the ban.**\nYour next message in this channel will be used as the reason. You have 2 minutes.', view=None)
    except discord.NotFound:
        confirm_msg = await ctx.send('📝 **Please provide a reason for the ban.**\nYour next message in this channel will be used as the reason. You have 2 minutes.')
    def reason_check(message):
        return message.author == ctx.author and message.channel == ctx.channel
    try:
        reason_message = await bot.wait_for('message', timeout=120.0, check=reason_check)
        reason_text = reason_message.content
        try:
            await reason_message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
    except asyncio.TimeoutError:
        await confirm_msg.edit(content='⌛ Reason prompt timed out. Ban command cancelled.', view=None)
        return
    await confirm_msg.edit(content=f'⏳ Banning {len(users_to_ban)} user(s) with reason: *{reason_text}*', view=None)
    successes = []
    failures = failed_to_find
    final_reason = f'Banned by {ctx.author.name} (ID: {ctx.author.id}): {reason_text}'
    for user_to_ban in users_to_ban:
        try:
            await ctx.guild.ban(user_to_ban, reason=final_reason, delete_message_days=0)
            successes.append(f'`{user_to_ban.name}` (ID: {user_to_ban.id})')
            logger.info(f'Successfully banned user {user_to_ban.name} (ID: {user_to_ban.id}) on behalf of {ctx.author.name}.')
        except discord.Forbidden:
            failures.append(f'`{user_to_ban.name}` (Missing permissions to ban this user)')
        except discord.HTTPException as e:
            failures.append(f'`{user_to_ban.name}` (Failed due to a network error: {e})')
        except Exception as e:
            failures.append(f'`{user_to_ban.name}` (An unexpected error occurred: {e})')
            logger.error(f'Unexpected error during !ban for {user_to_ban.name}: {e}', exc_info=True)
    response_message = ''
    if successes:
        response_message += f'✅ **Successfully banned:**\n' + '\n'.join((f'- {s}' for s in successes))
    if failures:
        response_message += f'\n\n❌ **Failed actions:**\n' + '\n'.join((f'- {f}' for f in failures))
    await confirm_msg.edit(content=response_message)
@bot.command(name='unban')
@require_allowed_user()
@handle_errors
async def unban(ctx, *, user_ids_str: str):
    if not user_ids_str:
        await ctx.send('Usage: `!unban <user_id_1>, <user_id_2>, ...`')
        return
    user_ids = [uid.strip() for uid in user_ids_str.split(',')]
    users_to_unban = []
    failed_to_find = []
    banned_users = {entry.user.id: entry.user async for entry in ctx.guild.bans()}
    for user_id in user_ids:
        try:
            user_id_int = int(user_id)
            if user_id_int in banned_users:
                users_to_unban.append(banned_users[user_id_int])
            else:
                failed_to_find.append(f'`{user_id}` (User is not banned or does not exist)')
        except ValueError:
            failed_to_find.append(f'`{user_id}` (Invalid ID format)')
    if not users_to_unban:
        await ctx.send('Could not find any valid banned users to unban.\n' + '\n'.join(failed_to_find))
        return
    user_list_str = '\n'.join([f'- **{user.name}** (`{user.id}`)' for user in users_to_unban])
    confirm_msg_content = f'⚠️ **Are you sure you want to unban the following user(s)?**\n{user_list_str}\n\nReact with ✅ to confirm or ❌ to cancel.'
    confirm_msg = await ctx.send(confirm_msg_content)
    for emoji in ('✅', '❌'):
        await confirm_msg.add_reaction(emoji)
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in {'✅', '❌'} and (reaction.message.id == confirm_msg.id)
    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=60.0, check=check)
        if str(reaction.emoji) == '❌':
            await confirm_msg.edit(content='🟢 Unban command cancelled.', view=None)
            return
    except asyncio.TimeoutError:
        await confirm_msg.edit(content='⌛ Unban command timed out.', view=None)
        return
    await confirm_msg.edit(content='⏳ Unbanning users...', view=None)
    successes = []
    failures = failed_to_find
    for user_to_unban in users_to_unban:
        try:
            reason = f'Unbanned by {ctx.author.name} (ID: {ctx.author.id}) via bot command.'
            await ctx.guild.unban(user_to_unban, reason=reason)
            successes.append(f'`{user_to_unban.name}` (ID: {user_to_unban.id})')
        except Exception as e:
            failures.append(f'`{user_to_unban.name}` (Failed to unban: {e})')
    response_message = ''
    if successes:
        response_message += f'✅ **Successfully unbanned:**\n' + '\n'.join((f'- {s}' for s in successes))
    if failures:
        response_message += f'\n\n❌ **Failed actions:**\n' + '\n'.join((f'- {f}' for f in failures))
    await confirm_msg.edit(content=response_message)
@bot.command(name='unbanall')
@require_allowed_user()
@handle_errors
async def unbanall(ctx):
    ban_entries = [entry async for entry in ctx.guild.bans()]
    if not ban_entries:
        await ctx.send('There are no users currently banned from this server.')
        return
    confirm_msg_content = f'⚠️ **CRITICAL ACTION** ⚠️\n\nAre you sure you want to unban all **{len(ban_entries)}** users from the server? This cannot be undone.\n\nReact with ✅ to confirm or ❌ to cancel.'
    confirm_msg = await ctx.send(confirm_msg_content)
    for emoji in ('✅', '❌'):
        await confirm_msg.add_reaction(emoji)
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in {'✅', '❌'} and (reaction.message.id == confirm_msg.id)
    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=60.0, check=check)
        if str(reaction.emoji) == '❌':
            await confirm_msg.edit(content='🟢 Unban All command cancelled.', view=None)
            return
    except asyncio.TimeoutError:
        await confirm_msg.edit(content='⌛ Unban All command timed out.', view=None)
        return
    await confirm_msg.edit(content=f'⏳ Unbanning all {len(ban_entries)} users...', view=None)
    success_count = 0
    failures = []
    for ban_entry in ban_entries:
        try:
            reason = f'Mass unban by {ctx.author.name} (ID: {ctx.author.id}).'
            await ctx.guild.unban(ban_entry.user, reason=reason)
            success_count += 1
            await asyncio.sleep(1)
        except Exception as e:
            failures.append(f'`{ban_entry.user.name}` (ID: {ban_entry.user.id}) - Error: {e}')
    response_message = f'✅ **Finished. Unbanned {success_count} of {len(ban_entries)} users.**'
    if failures:
        response_message += f'\n\n❌ **Failed to unban:**\n' + '\n'.join((f'- {f}' for f in failures))
    await confirm_msg.edit(content=response_message)
@bot.command(name='role')
@require_admin_preconditions()
@handle_errors
async def role_members(ctx, role: discord.Role):
    await helper.show_role_members(ctx, role)
@role_members.error
async def role_members_error(ctx, error: Exception) -> None:
    if isinstance(error, commands.RoleNotFound):
        await ctx.send(f'Could not find a role matching: `{error.argument}`')
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send('Usage: `!role <@role or role_name or role_id>`')
    elif isinstance(error, commands.CheckFailure):
        await ctx.send('⛔ You do not have permission to use this command.', delete_after=10)
    else:
        logger.error(f'Error in role command: {error}', exc_info=True)
        await ctx.send('An unexpected error occurred.')
@bot.command(name='bans', aliases=['banned'])
@require_allowed_user()
@handle_errors
async def bans(ctx) -> None:
    await helper.show_bans(ctx)
@bot.command(name='top')
@require_allowed_user()
@handle_errors
async def top_members(ctx) -> None:
    await helper.show_top_members(ctx)
@bot.command(name='info', aliases=['about'])
@require_user_preconditions()
@handle_errors
async def info(ctx) -> None:
    await helper.show_info(ctx)
@bot.command(name='roles')
@require_allowed_user()
@handle_errors
async def roles(ctx) -> None:
    await helper.list_roles(ctx)
@bot.command(name='admin', aliases=['owner', 'admins', 'owners'])
@require_allowed_user()
@handle_errors
async def admin(ctx) -> None:
    await helper.show_admin_list(ctx)
@bot.command(name='commands')
@require_admin_preconditions()
@handle_errors
async def commands_list(ctx) -> None:
    await helper.show_commands_list(ctx)
@bot.command(name='whois')
@require_allowed_user()
@handle_errors
async def whois(ctx) -> None:
    await helper.show_whois(ctx)
@bot.command(name='rtimeouts')
@require_admin_preconditions()
@handle_errors
async def remove_timeouts(ctx) -> None:
    await helper.remove_timeouts(ctx)
@bot.command(name='rules')
@require_user_preconditions()
@handle_errors
async def rules(ctx) -> None:
    if not getattr(ctx, 'from_button', False):
        await announce_command_usage(ctx, f'!{ctx.invoked_with}')
        await helper.show_rules(ctx)
@bot.command(name='timeouts')
@require_allowed_user()
@handle_errors
async def timeouts(ctx) -> None:
    await helper.show_timeouts(ctx)
@bot.command(name='times')
@require_allowed_user()
@handle_errors
async def time_report(ctx) -> None:
    await helper.show_times_report(ctx)
@bot.command(name='stats')
@require_allowed_user()
@handle_errors
async def analytics_report(ctx) -> None:
    await helper.show_analytics_report(ctx)
@bot.command(name='join')
@require_allowed_user()
@handle_errors
async def join(ctx) -> None:
    await helper.send_join_invites(ctx)
@bot.command(name='clearstats')
@require_allowed_user()
@handle_errors
async def clear_stats(ctx) -> None:
    record_command_usage(state.analytics, '!clearstats')
    record_command_usage_by_user(state.analytics, ctx.author.id, '!clearstats')
    await helper.clear_stats(ctx)
@bot.command(name='clearwhois')
@require_allowed_user()
@handle_errors
async def clear_whois(ctx) -> None:
    record_command_usage(state.analytics, '!clearwhois')
    record_command_usage_by_user(state.analytics, ctx.author.id, '!clearwhois')
    await helper.clear_whois_data(ctx)
@bot.command(name='display')
@require_admin_preconditions()
@handle_errors
async def display(ctx, member: discord.Member) -> None:
    await helper.show_user_display(ctx, member)
@display.error
async def display_error(ctx, error: Exception) -> None:
    if isinstance(error, commands.MemberNotFound):
        await ctx.send(f'Could not find a member in this server with the input: `{error.argument}`')
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send('Usage: `!display <@user or user_id>`')
    else:
        logger.error(f'Error in display command: {error}', exc_info=True)
        await ctx.send('An unexpected error occurred.')
@bot.command(name='move')
@handle_errors
async def move(ctx, member: discord.Member):
    author = ctx.author
    is_allowed_user = author.id in bot_config.ALLOWED_USERS
    is_move_role_user = False
    if isinstance(author, discord.Member):
        is_move_role_user = any((role.name in bot_config.MOVE_ROLE_NAME for role in author.roles))
    if not is_allowed_user and (not is_move_role_user):
        await ctx.send('⛔ You do not have permission to use this command.', delete_after=10)
        return
    if is_move_role_user and (not is_allowed_user):
        if ctx.channel.id != bot_config.COMMAND_CHANNEL_ID:
            await ctx.send(f'This command must be used in <#{bot_config.COMMAND_CHANNEL_ID}>.', delete_after=10)
            return
        if not is_user_in_streaming_vc_with_camera(author):
            await ctx.send('You must be in the Streaming VC with your camera on to use this command.', delete_after=10)
            return
    if not is_allowed_user:
        current_time = time.time()
        cooldown_duration = 3600
        async with state.cooldown_lock:
            last_used = state.move_command_cooldowns.get(author.id, 0)
            time_left = cooldown_duration - (current_time - last_used)
            if time_left > 0:
                await ctx.send(f'You can use this command again in {int(time_left // 60)} minutes.', delete_after=10)
                return
    streaming_vc = ctx.guild.get_channel(bot_config.STREAMING_VC_ID)
    punishment_vc = ctx.guild.get_channel(bot_config.PUNISHMENT_VC_ID)
    if not streaming_vc or not punishment_vc:
        await ctx.send('❌ Error: Streaming or Punishment VC not configured correctly.', delete_after=10)
        logger.error('!move command failed: STREAMING_VC_ID or PUNISHMENT_VC_ID is invalid.')
        return
    if member.id in bot_config.ALLOWED_USERS:
        await ctx.send(f'⛔ You cannot move an Allowed User ({member.display_name}).', delete_after=10)
        return
    if not member.voice or member.voice.channel != streaming_vc:
        await ctx.send(f'❌ {member.display_name} is not currently in the Streaming VC.', delete_after=10)
        return
    try:
        await member.move_to(punishment_vc, reason=f'Moved by {author.name} (Sleeping)')
        logger.info(f'{author.name} moved {member.name} to Punishment VC.')
    except discord.Forbidden:
        await ctx.send(f'❌ I do not have permissions to move {member.name}.', delete_after=10)
        return
    except Exception as e:
        await ctx.send(f'❌ An error occurred while moving {member.name}.', delete_after=10)
        logger.error(f'Failed to execute !move: {e}', exc_info=True)
        return
    if not is_allowed_user:
        async with state.cooldown_lock:
            state.move_command_cooldowns[author.id] = time.time()
    record_command_usage(state.analytics, '!move')
    record_command_usage_by_user(state.analytics, author.id, '!move')
    await helper.send_punishment_vc_notification(member=member, reason='They are sleeping', moderator_name=author.mention)
    try:
        await ctx.message.add_reaction('✅')
    except Exception:
        pass
@move.error
async def move_error(ctx, error: Exception) -> None:
    if isinstance(error, commands.MemberNotFound):
        await ctx.send(f'Could not find a member in this server with the input: `{error.argument}`')
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send('Usage: `!move <@user or user_id>`')
    elif isinstance(error, commands.CheckFailure):
        await ctx.send('⛔ You do not have permission to use this command.', delete_after=10)
    else:
        logger.error(f'Error in move command: {error}', exc_info=True)
        await ctx.send('An unexpected error occurred.')
if __name__ == '__main__':
    required_vars = ['BOT_TOKEN']
    if (missing := [var for var in required_vars if not os.getenv(var)]):
        logger.critical(f"Missing environment variables: {', '.join(missing)}")
        sys.exit(1)
    def handle_shutdown(signum, _frame):
        logger.info('Graceful shutdown initiated by signal')
        if not getattr(bot, '_is_shutting_down', False):
            bot.loop.create_task(_initiate_shutdown(None))
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    try:
        bot.run(os.getenv('BOT_TOKEN'))
    except discord.LoginFailure as e:
        logger.critical(f'Invalid token: {e}')
        sys.exit(1)
    except Exception as e:
        logger.critical(f'Fatal error during bot run: {e}', exc_info=True)
        raise
    finally:
        logger.info('Starting final shutdown process...')
        async def unregister_all_hotkeys():
            async def unregister_hotkey(enabled, combo, name):
                if enabled:
                    try:
                        await asyncio.to_thread(keyboard.remove_hotkey, combo)
                    except Exception:
                        pass
            if (bot_config := globals().get('bot_config')):
                await unregister_hotkey(bot_config.ENABLE_GLOBAL_HOTKEY, bot_config.GLOBAL_HOTKEY_COMBINATION, 'skip')
                await unregister_hotkey(bot_config.ENABLE_GLOBAL_MSKIP, bot_config.GLOBAL_HOTKEY_MSKIP, 'mskip')
                await unregister_hotkey(bot_config.ENABLE_GLOBAL_MPAUSE, bot_config.GLOBAL_HOTKEY_MPAUSE, 'mpause')
                await unregister_hotkey(bot_config.ENABLE_GLOBAL_MVOLUP, bot_config.GLOBAL_HOTKEY_MVOLUP, 'mvolup')
                await unregister_hotkey(bot_config.ENABLE_GLOBAL_MVOLDOWN, bot_config.GLOBAL_HOTKEY_MVOLDOWN, 'mvoldown')
        if 'keyboard' in globals():
            asyncio.run(unregister_all_hotkeys())
        if 'omegle_handler' in globals() and omegle_handler.driver:
            asyncio.run(omegle_handler.close())
        if 'state' in globals():
            logger.info('Performing final state save...')
            asyncio.run(save_state_async())
        logger.info('Shutdown complete')