# tools.py
# This file contains utility functions, data classes, and the state management class
# that are shared across different parts of the bot.

import asyncio
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import discord
from discord.ext import commands
from loguru import logger

# --- LOGGER CONFIGURATION ---
# Configure the Loguru logger for rich, async-safe logging.
logger.remove() # Remove the default handler.

# This function will be used to shorten the name of verbose functions in the log.
def patch_record(record):
    if record["function"] == "on_voice_state_update":
        record["function"] = "VC_UPDATE" # Replace the long name with a short tag.
    
logger.patch(patch_record)

# Add a handler for console output with colors and a detailed format.
logger.add(sys.stdout, colorize=True, format="<green>{time:MM-DD-YYYY HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", enqueue=True)
# Add a handler to log to a file, with automatic rotation and compression.
logger.add("bot.log", rotation="10 MB", compression="zip", enqueue=True, level="INFO")


def sanitize_channel_name(channel_name: str) -> str:
    """Sanitizes channel names for logging by removing non-ASCII characters."""
    return ''.join(char for char in channel_name if ord(char) < 128)

async def log_command_usage(state: 'BotState', ctx_or_interaction: Any, command_name: str) -> None:
    """
    Logs the usage of a command or button interaction, with duplicate prevention.
    This helps reduce log spam from rapid clicks or command executions.
    """
    try:
        # Determine the source of the command (regular command, button, or message).
        if isinstance(ctx_or_interaction, commands.Context):
            user, channel, source = ctx_or_interaction.author, getattr(ctx_or_interaction.channel, 'name', 'DM'), 'command'
        elif isinstance(ctx_or_interaction, discord.Interaction):
            user, channel, source = ctx_or_interaction.user, getattr(ctx_or_interaction.channel, 'name', 'DM'), 'button'
        else: # Fallback for other types
            user, channel, source = ctx_or_interaction.author, getattr(ctx_or_interaction.channel, 'name', 'DM'), 'message'

        # Create a unique identifier for the command usage event, grouped into 10-second windows.
        timestamp = int(time.time())
        log_id = f"{user.id}-{command_name}-{timestamp//10}"

        # Atomically check if the command was already logged and log it if not.
        # If it returns False, it means it was a duplicate, so we exit.
        if not await state.check_and_log_command(log_id):
            return

        # Format and log the command usage information.
        safe_channel = sanitize_channel_name(channel)
        human_time = datetime.now(timezone.utc).strftime('%m-%d-%Y %H:%M:%S')
        # Use the user's nickname (display_name) for cleaner logs.
        user_nickname = getattr(user, 'display_name', user.name)
        logger.info(
            f"COMMAND USED: '{command_name}' by '{user_nickname}' "
            f"in #{safe_channel} at {human_time} [via {source}]"
        )
    except Exception as e:
        logger.error(f"Error logging command usage: {e}", exc_info=True)

def handle_errors(func: Any) -> Any:
    """
    A decorator for bot commands and events that provides centralized error handling and logging.
    This prevents the bot from crashing on unexpected errors within a command.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        ctx = None
        # Heuristically find the context (ctx) object from the function arguments.
        if args:
            if isinstance(args[0], (commands.Context, discord.Interaction)):
                ctx = args[0]
            elif hasattr(args[0], 'channel'): # Covers Message, Member, etc.
                ctx = args[0]

        # Log command usage if a valid context is found.
        if ctx and isinstance(ctx, commands.Context) and ctx.command and hasattr(ctx.bot, 'state'):
            await log_command_usage(ctx.bot.state, ctx, ctx.command.name)

        try:
            # Execute the original function.
            return await func(*args, **kwargs)
        except Exception as e:
            # If an error occurs, log it and notify the user.
            logger.error(f"Error in {func.__name__}: {e}", exc_info=True)
            if ctx and hasattr(ctx, "send"):
                try: await ctx.send("An unexpected error occurred while running that command.")
                except Exception as send_e: logger.error(f"Failed to send error message to context: {send_e}")
    return wrapper

def ordinal(n: int) -> str:
    """Converts an integer to its ordinal representation (e.g., 1 -> "1st", 2 -> "2nd")."""
    if 10 <= n % 100 <= 20: suffix = 'th'
    else: suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return str(n) + suffix

def format_duration(delta: Union[timedelta, int]) -> str:
    """
    Formats a timedelta or seconds into a human-readable string.
    - If duration is >= 1 month, shows years, months, and days (e.g., '1y 2mo 3d').
    - If duration is < 1 month but >= 1 day, shows days and hours (e.g., '5d 10h').
    - If duration is < 1 day, shows hours and minutes (e.g., '7h 42m').
    - Does not show zero values.
    - Returns "1m" for durations less than a minute.
    """
    if isinstance(delta, timedelta):
        total_seconds = int(delta.total_seconds())
    else:
        total_seconds = int(delta)

    if total_seconds < 60:
        return "1m"

    if total_seconds < 0:
        total_seconds = 0

    # Constants for time conversion
    SECONDS_IN_MINUTE = 60
    SECONDS_IN_HOUR = 60 * SECONDS_IN_MINUTE
    SECONDS_IN_DAY = 24 * SECONDS_IN_HOUR
    # Use a consistent average for month length for calculation (approx 30.44 days)
    SECONDS_IN_MONTH = int(30.4375 * SECONDS_IN_DAY)
    SECONDS_IN_YEAR = 365 * SECONDS_IN_DAY

    parts = []

    remainder = total_seconds

    years, remainder = divmod(remainder, SECONDS_IN_YEAR)
    if years > 0:
        parts.append(f"{years}y")

    months, remainder = divmod(remainder, SECONDS_IN_MONTH)
    if months > 0:
        parts.append(f"{months}mo")

    days, remainder = divmod(remainder, SECONDS_IN_DAY)
    if days > 0:
        parts.append(f"{days}d")

    # Only show hours if the total duration is less than a month.
    if total_seconds < SECONDS_IN_MONTH:
        hours, remainder = divmod(remainder, SECONDS_IN_HOUR)
        if hours > 0:
            parts.append(f"{hours}h")

    # Only show minutes if the total duration is less than a day.
    if total_seconds < SECONDS_IN_DAY:
        minutes, _ = divmod(remainder, SECONDS_IN_MINUTE)
        if minutes > 0:
            parts.append(f"{minutes}m")

    return " ".join(parts) if parts else "1m"


def get_discord_age(created_at: datetime) -> str:
    """Calculates the age of a Discord account or server membership in a human-readable format."""
    now = datetime.now(timezone.utc)
    delta = now - created_at
    return format_duration(delta)

# A set of commands that should be included in statistical tracking.
ALLOWED_STATS_COMMANDS = {
    "!stats", "!skip", "!refresh", "!rules", "!about", "!info", "!whois", "!rtimeouts", "!roles",
    "!join", "!top", "!commands", "!admin", "!admins", "!owner", "!owners", "!timeouts", "!times",
    "!rhush", "!rsecret", "!hush", "!secret", "!modon", "!modoff", "!banned", "!bans",
    "!clearstats", "!start", "!pause", "!clearwhois", "!msearch", "!mclear", "!mshuffle",
    "!mpauseplay", "!mskip", "!nowplaying", "!np", "!queue", "!q", "!playlist", "!volume",
    "!mon", "!moff", "!help", "!music", "!purge", "!shutdown", "!disable", "!enable",
    "!disablenotifications", "!enablenotifications", "!ban", "!unbanall", "!display"
}

def record_command_usage(analytics: Dict[str, Any], command_name: str) -> None:
    """Records the usage of a command in the analytics dictionary if it's in the allowed list."""
    if command_name not in ALLOWED_STATS_COMMANDS: return
    analytics["command_usage"][command_name] = analytics["command_usage"].get(command_name, 0) + 1

def record_command_usage_by_user(analytics: Dict[str, Any], user_id: int, command_name: str) -> None:
    """Records which user used which command for more detailed analytics."""
    if command_name not in ALLOWED_STATS_COMMANDS: return
    if user_id not in analytics["command_usage_by_user"]: analytics["command_usage_by_user"][user_id] = {}
    analytics["command_usage_by_user"][user_id][command_name] = analytics["command_usage_by_user"][user_id].get(command_name, 0) + 1

@dataclass
class BotConfig:
    """
    A dataclass to hold all configuration variables loaded from config.py.
    This provides type hinting and a single, structured object for configuration.
    It now handles default values for optional settings.
    """
    # Required Settings
    GUILD_ID: int
    COMMAND_CHANNEL_ID: int
    CHAT_CHANNEL_ID: int
    STREAMING_VC_ID: int
    PUNISHMENT_VC_ID: int
    OMEGLE_VIDEO_URL: str
    EDGE_USER_DATA_DIR: str

    # Optional Settings (with defaults)
    ALT_VC_ID: Optional[int]
    ALLOWED_USERS: Set[int]
    ADMIN_ROLE_NAME: List[str]
    JOIN_INVITE_MESSAGE: str
    ENABLE_GLOBAL_HOTKEY: bool
    GLOBAL_HOTKEY_COMBINATION: str
    COMMAND_COOLDOWN: int
    RULES_MESSAGE: str
    INFO_MESSAGES: List[str]
    CAMERA_OFF_ALLOWED_TIME: int
    TIMEOUT_DURATION_SECOND_VIOLATION: int
    TIMEOUT_DURATION_THIRD_VIOLATION: int
    STATS_EXCLUDED_USERS: Set[int]
    AUTO_STATS_CHAN: Optional[int]
    AUTO_STATS_HOUR_UTC: int
    AUTO_STATS_MINUTE_UTC: int
    MEDIA_ONLY_CHANNEL_ID: Optional[int]
    MOD_MEDIA: bool
    EDGE_DRIVER_PATH: Optional[str]
    EMPTY_VC_PAUSE: bool
    
    # Music Settings
    MUSIC_ENABLED: bool
    MUSIC_LOCATION: Optional[str]
    MUSIC_BOT_VOLUME: float
    MUSIC_MAX_VOLUME: float
    MUSIC_SUPPORTED_FORMATS: Tuple[str, ...]
    MUSIC_DEFAULT_ANNOUNCE_SONGS: bool
    NORMALIZE_LOCAL_MUSIC: bool
    ENABLE_GLOBAL_MSKIP: bool
    GLOBAL_HOTKEY_MSKIP: str
    ENABLE_GLOBAL_MPAUSE: bool
    GLOBAL_HOTKEY_MPAUSE: str
    ENABLE_GLOBAL_MVOLUP: bool
    GLOBAL_HOTKEY_MVOLUP: str
    ENABLE_GLOBAL_MVOLDOWN: bool
    GLOBAL_HOTKEY_MVOLDOWN: str

    @staticmethod
    def from_config_module(config_module: Any) -> 'BotConfig':
        """
        Creates a BotConfig instance from the config.py module.
        It uses `getattr` to provide default values for optional settings,
        making the config file cleaner and easier to manage.
        """
        # --- Default Messages ---
        default_rules = """## Welcome to the Server!
**Rule 1:** Be respectful to others.
**Rule 2:** Keep your camera on in the streaming voice channel.
**Rule 3:** No hateful or inappropriate content.
"""

        return BotConfig(
            # --- Required Settings ---
            GUILD_ID=getattr(config_module, 'GUILD_ID', None),
            COMMAND_CHANNEL_ID=getattr(config_module, 'COMMAND_CHANNEL_ID', None),
            CHAT_CHANNEL_ID=getattr(config_module, 'CHAT_CHANNEL_ID', None),
            STREAMING_VC_ID=getattr(config_module, 'STREAMING_VC_ID', None),
            PUNISHMENT_VC_ID=getattr(config_module, 'PUNISHMENT_VC_ID', None),
            OMEGLE_VIDEO_URL=getattr(config_module, 'OMEGLE_VIDEO_URL', None),
            EDGE_USER_DATA_DIR=getattr(config_module, 'EDGE_USER_DATA_DIR', None),

            # --- Optional Settings (with defaults) ---
            ALT_VC_ID=getattr(config_module, 'ALT_VC_ID', None),
            ALLOWED_USERS=getattr(config_module, 'ALLOWED_USERS', set()),
            ADMIN_ROLE_NAME=getattr(config_module, 'ADMIN_ROLE_NAME', []),
            JOIN_INVITE_MESSAGE=getattr(config_module, 'JOIN_INVITE_MESSAGE', ""),
            ENABLE_GLOBAL_HOTKEY=getattr(config_module, 'ENABLE_GLOBAL_HOTKEY', False),
            GLOBAL_HOTKEY_COMBINATION=getattr(config_module, 'GLOBAL_HOTKEY_COMBINATION', 'alt+grave'),
            COMMAND_COOLDOWN=getattr(config_module, 'COMMAND_COOLDOWN', 5),
            RULES_MESSAGE=getattr(config_module, 'RULES_MESSAGE', default_rules),
            INFO_MESSAGES=getattr(config_module, 'INFO_MESSAGES', []),
            CAMERA_OFF_ALLOWED_TIME=getattr(config_module, 'CAMERA_OFF_ALLOWED_TIME', 30),
            TIMEOUT_DURATION_SECOND_VIOLATION=getattr(config_module, 'TIMEOUT_DURATION_SECOND_VIOLATION', 60),
            TIMEOUT_DURATION_THIRD_VIOLATION=getattr(config_module, 'TIMEOUT_DURATION_THIRD_VIOLATION', 300),
            STATS_EXCLUDED_USERS=getattr(config_module, 'STATS_EXCLUDED_USERS', set()),
            AUTO_STATS_CHAN=getattr(config_module, 'AUTO_STATS_CHAN', None),
            AUTO_STATS_HOUR_UTC=getattr(config_module, 'AUTO_STATS_HOUR_UTC', 0),
            AUTO_STATS_MINUTE_UTC=getattr(config_module, 'AUTO_STATS_MINUTE_UTC', 0),
            MEDIA_ONLY_CHANNEL_ID=getattr(config_module, 'MEDIA_ONLY_CHANNEL_ID', None),
            MOD_MEDIA=getattr(config_module, 'MOD_MEDIA', True),
            EDGE_DRIVER_PATH=getattr(config_module, 'EDGE_DRIVER_PATH', None),
            EMPTY_VC_PAUSE=getattr(config_module, 'EMPTY_VC_PAUSE', True),

            # --- Music Settings (with defaults) ---
            MUSIC_ENABLED=getattr(config_module, 'MUSIC_ENABLED', False),
            MUSIC_LOCATION=getattr(config_module, 'MUSIC_LOCATION', None),
            MUSIC_BOT_VOLUME=getattr(config_module, 'MUSIC_BOT_VOLUME', 0.2),
            MUSIC_MAX_VOLUME=getattr(config_module, 'MUSIC_MAX_VOLUME', 1.0),
            MUSIC_SUPPORTED_FORMATS=getattr(config_module, 'MUSIC_SUPPORTED_FORMATS', ('.mp3', '.flac', '.wav', '.ogg', '.m4a')),
            MUSIC_DEFAULT_ANNOUNCE_SONGS=getattr(config_module, 'MUSIC_DEFAULT_ANNOUNCE_SONGS', False),
            NORMALIZE_LOCAL_MUSIC=getattr(config_module, 'NORMALIZE_LOCAL_MUSIC', True),
            ENABLE_GLOBAL_MSKIP=getattr(config_module, 'ENABLE_GLOBAL_MSKIP', False),
            GLOBAL_HOTKEY_MSKIP=getattr(config_module, 'GLOBAL_HOTKEY_MSKIP', 'grave'),
            ENABLE_GLOBAL_MPAUSE=getattr(config_module, 'ENABLE_GLOBAL_MPAUSE', False),
            GLOBAL_HOTKEY_MPAUSE=getattr(config_module, 'GLOBAL_HOTKEY_MPAUSE', 'grave'),
            ENABLE_GLOBAL_MVOLUP=getattr(config_module, 'ENABLE_GLOBAL_MVOLUP', False),
            GLOBAL_HOTKEY_MVOLUP=getattr(config_module, 'GLOBAL_HOTKEY_MVOLUP', ']'),
            ENABLE_GLOBAL_MVOLDOWN=getattr(config_module, 'ENABLE_GLOBAL_MVOLDOWN', False),
            GLOBAL_HOTKEY_MVOLDOWN=getattr(config_module, 'GLOBAL_HOTKEY_MVOLDOWN', '[')
        )

def build_embed(title: str, description: str, color: discord.Color) -> discord.Embed:
    """A simple helper function to build a basic Discord embed."""
    return discord.Embed(title=title, description=description, color=color)

async def build_role_update_embed(member: discord.Member, roles_gained: List[discord.Role], roles_lost: List[discord.Role]) -> discord.Embed:
    """Builds a detailed embed to announce a member's role change."""
    user = member # Default to the provided member object.
    try:
        # Fetching the full user object may provide more details, like the banner.
        user = await member.guild.fetch_member(member.id)
    except (discord.NotFound, Exception) as e:
        logger.warning(f"Could not fetch full member object for {member.name} during role update: {e}")

    banner_url = user.banner.url if hasattr(user, 'banner') and user.banner else None

    embed = discord.Embed(title=f"Role Update for {member.name}", description=f"{member.mention} had a role change.", color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    if banner_url: embed.set_image(url=banner_url)

    embed.add_field(name="Account Created", value=member.created_at.strftime('%m-%d-%Y'), inline=True)
    if member.joined_at: embed.add_field(name="Time in Server", value=get_discord_age(member.joined_at), inline=True)
    embed.add_field(name="User ID", value=str(member.id), inline=True)

    if roles_gained: embed.add_field(name="Roles Gained", value=" ".join([role.mention for role in roles_gained]), inline=False)
    if roles_lost: embed.add_field(name="Roles Lost", value=" ".join([role.mention for role in roles_lost]), inline=False)

    return embed

# Type Aliases for BotState clarity
Cooldowns = Dict[int, Tuple[float, bool]]
ViolationCounts = Dict[int, int]
ActiveTimeouts = Dict[int, Dict[str, Any]]
JoinHistory = List[Tuple[int, str, Optional[str], datetime]]
LeaveHistory = List[Tuple[int, str, Optional[str], datetime, Optional[str]]]
BanHistory = List[Tuple[int, str, Optional[str], datetime, str]]
KickHistory = List[Tuple[int, str, Optional[str], datetime, str, Optional[str], Optional[str]]]
UnbanHistory = List[Tuple[int, str, Optional[str], datetime, str]]
UntimeoutHistory = List[Tuple[int, str, Optional[str], datetime, str, Optional[str], Optional[int]]]
RoleChangeHistory = List[Tuple[int, str, List[str], List[str], datetime]]
AnalyticsData = Dict[str, Union[Dict[str, int], Dict[int, Dict[str, int]], int]]
VcTimeData = Dict[int, Dict[str, Any]]
ActiveVcSessions = Dict[int, float]
Playlists = Dict[str, List[Dict[str, Any]]]

@dataclass
class BotState:
    """
    A class to manage the bot's entire persistent and transient state.
    This includes everything from analytics and moderation data to cooldowns and voice channel session tracking.
    Using a class like this centralizes state management and makes it easier to save, load, and access data safely.
    """
    config: BotConfig

    # --- Concurrency Locks ---
    vc_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    analytics_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    moderation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    music_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    cooldown_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    music_startup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    # --- State Data ---
    cooldowns: Cooldowns = field(default_factory=dict)
    button_cooldowns: Cooldowns = field(default_factory=dict)
    last_omegle_command_time: float = 0.0
    camera_off_timers: Dict[int, float] = field(default_factory=dict)
    user_violations: ViolationCounts = field(default_factory=dict)
    hush_override_active: bool = False
    vc_moderation_active: bool = True
    notifications_enabled: bool = True
    users_received_rules: Set[int] = field(default_factory=set)
    users_with_dms_disabled: Set[int] = field(default_factory=set)
    failed_dm_users: Set[int] = field(default_factory=set)
    active_timeouts: ActiveTimeouts = field(default_factory=dict)
    pending_timeout_removals: Dict[int, bool] = field(default_factory=dict)
    recent_kick_timestamps: Dict[int, datetime] = field(default_factory=dict)
    recently_banned_ids: Set[int] = field(default_factory=set)
    recent_joins: JoinHistory = field(default_factory=list)
    recent_leaves: LeaveHistory = field(default_factory=list)
    recent_bans: BanHistory = field(default_factory=list)
    recent_kicks: KickHistory = field(default_factory=list)
    recent_unbans: UnbanHistory = field(default_factory=list)
    recent_untimeouts: UntimeoutHistory = field(default_factory=list)
    recent_role_changes: RoleChangeHistory = field(default_factory=list)
    omegle_disabled_users: Set[int] = field(default_factory=set)
    analytics: AnalyticsData = field(default_factory=lambda: {"command_usage": {}, "command_usage_by_user": {}, "violation_events": 0})
    recently_logged_commands: Set[str] = field(default_factory=set)
    last_auto_pause_time: float = 0.0
    vc_time_data: VcTimeData = field(default_factory=dict)
    active_vc_sessions: ActiveVcSessions = field(default_factory=dict)
    
    # Music state
    music_enabled: bool = True
    all_songs: List[str] = field(default_factory=list)
    shuffle_queue: List[str] = field(default_factory=list)
    search_queue: List[Dict[str, Any]] = field(default_factory=list)
    active_playlist: List[Dict[str, Any]] = field(default_factory=list)
    current_song: Optional[Dict[str, Any]] = None # {'title': str, 'path': str, 'is_stream': bool}
    is_music_playing: bool = False
    is_music_paused: bool = False
    is_processing_song: bool = False # FIX: New flag to prevent watchdog race condition
    music_mode: str = 'shuffle'  # 'shuffle', 'alphabetical', 'loop'
    music_volume: float = 0.2
    playlists: Playlists = field(default_factory=dict)

    # Window geometry state
    window_size: Optional[Dict[str, int]] = field(default=None)
    window_position: Optional[Dict[str, int]] = field(default=None)

    # Transient state (not saved to disk)
    announcement_context: Optional[Any] = None
    play_next_override: bool = False
    stop_after_clear: bool = False
    leave_buffer: List[discord.Member] = field(default_factory=list, init=False)
    leave_batch_task: Optional[asyncio.Task] = field(default=None, init=False)

    def __post_init__(self):
        if self.config:
            self.music_volume = self.config.MUSIC_BOT_VOLUME
            self.music_enabled = self.config.MUSIC_ENABLED


    def to_dict(self, guild: Optional[discord.Guild], active_vc_sessions_to_save: dict, current_time: float) -> dict:
        """
        Serializes the bot's state into a JSON-compatible dictionary for saving to a file.
        This method handles converting complex objects like sets and datetimes into storable formats.
        """
        vc_data_to_save = {user_id: data.copy() for user_id, data in self.vc_time_data.items()}

        # Before saving, "end" all active VC sessions at the current time so their duration is included.
        for user_id, session_start in active_vc_sessions_to_save.items():
            session_duration = current_time - session_start
            if user_id not in vc_data_to_save:
                member = guild.get_member(user_id) if guild else None
                username = member.name if member else "Unknown"
                display_name = member.display_name if member else "Unknown"
                vc_data_to_save[user_id] = {"total_time": 0, "sessions": [], "username": username, "display_name": display_name}
            vc_data_to_save[user_id]["sessions"].append({"start": session_start, "end": current_time, "duration": session_duration, "vc_name": "Streaming VC"})
            vc_data_to_save[user_id]["total_time"] += session_duration

        def clean_song_dict(song_dict: Optional[Dict]) -> Optional[Dict]:
            """A helper to remove non-serializable objects from a song dictionary before saving."""
            if not song_dict:
                return None
            # Return a copy of the dictionary, excluding the 'ctx' key.
            return {key: value for key, value in song_dict.items() if key != 'ctx'}

        # Return a dictionary with all the state data converted to JSON-friendly types.
        return {
            "analytics": self.analytics,
            "users_received_rules": list(self.users_received_rules),
            "user_violations": self.user_violations,
            "active_timeouts": self.active_timeouts,
            "notifications_enabled": self.notifications_enabled,
            "recent_joins": [
                {"id": e[0], "name": e[1], "display_name": e[2], "timestamp": e[3].isoformat()}
                for e in self.recent_joins
            ],
            "recent_leaves": [
                {"id": e[0], "name": e[1], "display_name": e[2], "timestamp": e[3].isoformat(), "roles": e[4]}
                for e in self.recent_leaves
            ],
            "recent_role_changes": [
                {"id": e[0], "name": e[1], "gained": e[2], "lost": e[3], "timestamp": e[4].isoformat()}
                for e in self.recent_role_changes
            ],
            "users_with_dms_disabled": list(self.users_with_dms_disabled),
            "recent_bans": [
                {"id": e[0], "name": e[1], "display_name": e[2], "timestamp": e[3].isoformat(), "reason": e[4]}
                for e in self.recent_bans
            ],
            "recent_kicks": [
                {"id": e[0], "name": e[1], "display_name": e[2], "timestamp": e[3].isoformat(), "reason": e[4], "moderator": e[5], "roles": e[6]}
                for e in self.recent_kicks
            ],
            "recent_unbans": [
                {"id": e[0], "name": e[1], "display_name": e[2], "timestamp": e[3].isoformat(), "moderator": e[4]}
                for e in self.recent_unbans
            ],
            "recent_untimeouts": [
                {"id": e[0], "name": e[1], "display_name": e[2], "timestamp": e[3].isoformat(), "reason": e[4], "moderator_name": e[5], "moderator_id": e[6]}
                for e in self.recent_untimeouts
            ],
            "omegle_disabled_users": list(self.omegle_disabled_users),
            "recent_kick_timestamps": {k: v.isoformat() for k, v in self.recent_kick_timestamps.items()},
            "vc_time_data": {str(user_id): data for user_id, data in vc_data_to_save.items()},
            "active_vc_sessions": {}, # Active sessions are ephemeral and are not reloaded, so save an empty dict.

            # Music state (now cleaned of non-serializable objects)
            "music_enabled": self.music_enabled,
            "music_mode": self.music_mode,
            "search_queue": [clean_song_dict(s) for s in self.search_queue],
            "active_playlist": [clean_song_dict(s) for s in self.active_playlist],
            "current_song": clean_song_dict(self.current_song),
            "music_volume": self.music_volume,
            "playlists": self.playlists,
            
            # Window geometry state
            "window_size": self.window_size,
            "window_position": self.window_position,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], config: BotConfig) -> 'BotState':
        """
        A class method to deserialize a dictionary (from a JSON file) into a full BotState object.
        This method reconstructs the state, converting stored formats back into their proper types.
        """
        state = cls(config=config)

        analytics = data.get("analytics", {"command_usage": {}, "command_usage_by_user": {}, "violation_events": 0})
        if "command_usage_by_user" in analytics:
            analytics["command_usage_by_user"] = {int(k): v for k, v in analytics.get("command_usage_by_user", {}).items()}
        state.analytics = analytics

        state.user_violations = {int(k): v for k, v in data.get("user_violations", {}).items()}
        state.active_timeouts = {int(k): v for k, v in data.get("active_timeouts", {}).items()}
        state.notifications_enabled = data.get("notifications_enabled", True)
        state.users_received_rules = set(data.get("users_received_rules", []))
        state.users_with_dms_disabled = set(data.get("users_with_dms_disabled", []))
        state.omegle_disabled_users = set(data.get("omegle_disabled_users", []))

        state.recent_joins = [(e["id"], e["name"], e["display_name"], datetime.fromisoformat(e["timestamp"])) for e in data.get("recent_joins", [])]
        state.recent_leaves = [(e["id"], e["name"], e["display_name"], datetime.fromisoformat(e["timestamp"]), e["roles"]) for e in data.get("recent_leaves", [])]
        state.recent_role_changes = [(e["id"], e["name"], e["gained"], e["lost"], datetime.fromisoformat(e["timestamp"])) for e in data.get("recent_role_changes", [])]
        state.recent_bans = [(e["id"], e["name"], e["display_name"], datetime.fromisoformat(e["timestamp"]), e["reason"]) for e in data.get("recent_bans", [])]
        state.recent_kicks = [(e["id"], e["name"], e["display_name"], datetime.fromisoformat(e["timestamp"]), e["reason"], e["moderator"], e["roles"]) for e in data.get("recent_kicks", [])]
        state.recent_unbans = [(e["id"], e["name"], e["display_name"], datetime.fromisoformat(e["timestamp"]), e["moderator"]) for e in data.get("recent_unbans", [])]
        state.recent_untimeouts = [(e["id"], e["name"], e["display_name"], datetime.fromisoformat(e["timestamp"]), e["reason"], e.get("moderator_name"), e.get("moderator_id")) for e in data.get("recent_untimeouts", [])]

        state.recent_kick_timestamps = {int(k): datetime.fromisoformat(v) for k, v in data.get("recent_kick_timestamps", {}).items()}
        state.vc_time_data = {int(k): v for k, v in data.get("vc_time_data", {}).items()}
        state.active_vc_sessions = {}

        # Music state
        state.music_enabled = data.get("music_enabled", config.MUSIC_ENABLED if config else True)
        state.music_mode = data.get("music_mode", 'shuffle')
        state.search_queue = data.get("search_queue", [])
        state.active_playlist = data.get("active_playlist", [])
        state.current_song = data.get("current_song", None)
        state.music_volume = data.get("music_volume", config.MUSIC_BOT_VOLUME if config else 0.2)
        state.playlists = data.get("playlists", {})
        
        # Window geometry state
        state.window_size = data.get("window_size", None)
        state.window_position = data.get("window_position", None)
        
        return state

    async def check_and_log_command(self, log_id: str) -> bool:
        """
        Atomically checks if a command has been logged recently and logs it if not.
        Returns True if the command was newly logged, False if it was a duplicate.
        """
        async with self.cooldown_lock:
            if log_id in self.recently_logged_commands:
                return False  # It's a duplicate
            self.recently_logged_commands.add(log_id)
            return True  # It's new, and we logged it

    async def clean_old_entries(self) -> None:
        """
        A unified cleanup function that runs periodically to prune old data from the state.
        This is crucial for managing memory usage over long uptimes. It trims event histories
        to the last 7 days and enforces size limits on large datasets.
        """
        current_time = time.time()
        now = datetime.now(timezone.utc)
        seven_days_ago_dt = now - timedelta(days=7)

        # Clean up expired cooldowns.
        async with self.cooldown_lock:
            self.cooldowns = {k: v for k, v in self.cooldowns.items() if current_time - v[0] < (self.config.COMMAND_COOLDOWN * 2)}
            self.button_cooldowns = {k: v for k, v in self.button_cooldowns.items() if current_time - v[0] < (self.config.COMMAND_COOLDOWN * 2)}

        # Clean up expired moderation data.
        async with self.moderation_lock:
            self.active_timeouts = {k: v for k, v in self.active_timeouts.items() if v.get('timeout_end', float('inf')) > current_time}
            self.recent_kick_timestamps = {k: v for k, v in self.recent_kick_timestamps.items() if now - v < timedelta(days=7)}
            # Simple clear for large user sets to prevent unbounded growth.
            for dataset in [self.failed_dm_users, self.users_with_dms_disabled, self.users_received_rules]:
                if len(dataset) > 1000: dataset.clear()
            # Clean up the transient banned ID set
            if len(self.recently_banned_ids) > 200:
                self.recently_banned_ids.clear()

        # Clean up old VC time data.
        async with self.vc_lock:
            self.camera_off_timers = {k: v for k, v in self.camera_off_timers.items() if current_time - v < (self.config.CAMERA_OFF_ALLOWED_TIME * 2)}
            seven_days_ago_ts = current_time - (7 * 24 * 3600)
            self.vc_time_data = {
                user_id: {**data, "sessions": [s for s in data.get("sessions", []) if s.get("end", 0) > seven_days_ago_ts]}
                for user_id, data in self.vc_time_data.items()
                if any(s.get("end", 0) > seven_days_ago_ts for s in data.get("sessions", []))
            }

        # Trim analytics data if it grows too large.
        async with self.analytics_lock:
            if isinstance(self.analytics.get("command_usage_by_user"), dict) and len(self.analytics["command_usage_by_user"]) > 1000:
                user_usage_sorted = sorted(self.analytics["command_usage_by_user"].items(), key=lambda x: sum(x[1].values()), reverse=True)
                self.analytics["command_usage_by_user"] = dict(user_usage_sorted[:1000])
            if isinstance(self.analytics.get("command_usage"), dict) and len(self.analytics["command_usage"]) > 100:
                commands_sorted = sorted(self.analytics["command_usage"].items(), key=lambda x: x[1], reverse=True)
                self.analytics["command_usage"] = dict(commands_sorted[:100])

        # Define limits for historical event lists.
        list_specs = {
            'recent_joins': (3, 200), 'recent_leaves': (3, 200), 'recent_bans': (3, 200),
            'recent_kicks': (3, 200), 'recent_unbans': (3, 200), 'recent_untimeouts': (3, 200),
            'recent_role_changes': (4, 200)
        }

        # Clean all historical event lists based on the defined specs.
        for list_name, (time_idx, max_entries) in list_specs.items():
            async with self.moderation_lock:
                lst = getattr(self, list_name)
                cleaned = [entry for entry in lst if len(entry) > time_idx and entry[time_idx] > seven_days_ago_dt][-max_entries:]
                setattr(self, list_name, cleaned)

        # Clear the temporary command log cache.
        if len(self.recently_logged_commands) > 5000:
            async with self.cooldown_lock:
                self.recently_logged_commands.clear()