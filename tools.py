# tools.py

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

# --- Loguru Configuration ---

# Remove default logger to configure our own
logger.remove()

def patch_record(record):
    """
    Patcher function for Loguru to rename a noisy function in the logs.
    """
    if record["function"] == "on_voice_state_update":
        record["function"] = "VC_UPDATE"


# Apply the patch
logger.patch(patch_record)

# Add a logger for stdout (the console) with colors and a specific format
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time:MM-DD-YYYY HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    enqueue=True,  # Asynchronous logging
)

# Add a logger for a rotating file ('bot.log')
logger.add(
    "bot.log",
    rotation="10 MB",  # New file every 10 MB
    compression="zip",  # Compress old log files
    enqueue=True,
    level="INFO",  # Log INFO level and above
)

# Add a separate logger for ban-specific events
logger.add(
    "ban.log",
    rotation="10 MB",
    compression="zip",
    enqueue=True,
    level="INFO",
    # Only log messages that have 'BAN_LOG' in their 'extra' data
    filter=lambda record: record["extra"].get("BAN_LOG", False),
    format="<green>{time:MM-DD-YYYY HH:mm:ss}</green> | <level>{message}</level>",
)


# --- Utility Functions ---

def sanitize_channel_name(channel_name: str) -> str:
    """
    Removes non-ASCII characters from a channel name for safe logging.
    """
    return "".join((char for char in channel_name if ord(char) < 128))


async def log_command_usage(
    state: "BotState", ctx_or_interaction: Any, command_name: str
) -> None:
    """
    Logs the usage of a command or button press, with deduplication.
    """
    try:
        # Determine user, channel, and source from context or interaction
        if isinstance(ctx_or_interaction, commands.Context):
            user, channel, source = (
                ctx_or_interaction.author,
                getattr(ctx_or_interaction.channel, "name", "DM"),
                "command",
            )
        elif isinstance(ctx_or_interaction, discord.Interaction):
            user, channel, source = (
                ctx_or_interaction.user,
                getattr(ctx_or_interaction.channel, "name", "DM"),
                "button",
            )
        else:
            # Fallback for other types
            user, channel, source = (
                ctx_or_interaction.author,
                getattr(ctx_or_interaction.channel, "name", "DM"),
                "message",
            )

        timestamp = int(time.time())
        # Create a unique-ish ID to prevent logging the same command multiple times
        # in a short window (e.g., from both a button and command handler)
        log_id = f"{user.id}-{command_name}-{timestamp // 10}"

        # Check and log atomically
        if not await state.check_and_log_command(log_id):
            return  # Already logged this command recently

        safe_channel = sanitize_channel_name(channel)
        human_time = datetime.now(timezone.utc).strftime("%m-%d-%Y %H:%M:%S")
        user_nickname = getattr(user, "display_name", user.name)

        logger.info(
            f"COMMAND USED: '{command_name}' by '{user_nickname}' in #{safe_channel} at {human_time} [via {source}]"
        )
    except Exception as e:
        logger.error(f"Error logging command usage: {e}", exc_info=True)


def handle_errors(func: Any) -> Any:
    """
    A decorator that wraps bot commands and events to provide centralized
    error handling and logging.
    """

    @wraps(func)
    async def wrapper(*args, **kwargs):
        ctx = None
        # Find the context (ctx or interaction) from the function arguments
        if args:
            if isinstance(args[0], (commands.Context, discord.Interaction)):
                ctx = args[0]
            elif hasattr(args[0], "channel"):  # Handle `on_message` etc.
                ctx = args[0]

        # Log command usage if it's a command context
        if (
            ctx
            and isinstance(ctx, commands.Context)
            and ctx.command
            and hasattr(ctx.bot, "state")
        ):
            await log_command_usage(ctx.bot.state, ctx, ctx.command.name)

        try:
            # Attempt to run the original function
            return await func(*args, **kwargs)
        except Exception as e:
            # --- Error Handling ---
            command_name = func.__name__
            reply_target = None
            if ctx:
                reply_target = ctx
                if isinstance(ctx, commands.Context):
                    command_name = ctx.invoked_with or command_name
                elif isinstance(ctx, discord.Interaction):
                    if ctx.data and "custom_id" in ctx.data:
                        command_name = ctx.data["custom_id"].split(":")[0]
                    elif ctx.command and ctx.command.name:
                        command_name = ctx.command.name

            # Log the full error
            logger.error(f"Error in {command_name}: {e}", exc_info=True)

            # Send a generic error message to the user
            error_message = (
                f"An unexpected error occurred while running **{command_name}**."
            )
            if reply_target:
                try:
                    if isinstance(reply_target, discord.Interaction):
                        if reply_target.response.is_done():
                            await reply_target.followup.send(
                                error_message, ephemeral=True
                            )
                        else:
                            await reply_target.response.send_message(
                                error_message, ephemeral=True
                            )
                    elif hasattr(reply_target, "send"):
                        await reply_target.send(error_message)
                except Exception as send_e:
                    logger.error(
                        f"Failed to send error message to context/interaction: {send_e}"
                    )

    return wrapper


def ordinal(n: int) -> str:
    """Returns a number as an ordinal string (e.g., 1 -> '1st', 2 -> '2nd')."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return str(n) + suffix


def format_duration(delta: Union[timedelta, int]) -> str:
    """
    Formats a duration (in seconds or timedelta) into a human-readable
    string like "1y 2mo 3d 4h 5m".
    """
    if isinstance(delta, timedelta):
        total_seconds = int(delta.total_seconds())
    else:
        total_seconds = int(delta)

    if total_seconds < 60:
        return "1min"  # <--- CHANGE 1: "1m" to "1min"
    if total_seconds < 0:
        total_seconds = 0

    # Define time units in seconds
    SECONDS_IN_MINUTE = 60
    SECONDS_IN_HOUR = 60 * SECONDS_IN_MINUTE
    SECONDS_IN_DAY = 24 * SECONDS_IN_HOUR
    SECONDS_IN_MONTH = int(30.4375 * SECONDS_IN_DAY)  # Average month
    SECONDS_IN_YEAR = 365 * SECONDS_IN_DAY

    parts = []
    remainder = total_seconds

    # Calculate years, months, days
    years, remainder = divmod(remainder, SECONDS_IN_YEAR)
    if years > 0:
        parts.append(f"{years}y")

    months, remainder = divmod(remainder, SECONDS_IN_MONTH)
    if months > 0:
        parts.append(f"{months}mo")

    days, remainder = divmod(remainder, SECONDS_IN_DAY)
    if days > 0:
        parts.append(f"{days}d")

    # Only show hours if duration is less than a month
    if total_seconds < SECONDS_IN_MONTH:
        hours, remainder = divmod(remainder, SECONDS_IN_HOUR)
        if hours > 0:
            parts.append(f"{hours}h")

    # Only show minutes if duration is less than a day
    if total_seconds < SECONDS_IN_DAY:
        minutes, _ = divmod(remainder, SECONDS_IN_MINUTE)
        if minutes > 0:
            parts.append(f"{minutes}min")  # <--- CHANGE 2: "{minutes}m" to "{minutes}min"

    return " ".join(parts) if parts else "1min"  # <--- CHANGE 3: "1m" to "1min"


def get_discord_age(created_at: datetime) -> str:
    """
    Calculates the age of a Discord account/object from its creation timestamp.
    """
    now = datetime.now(timezone.utc)
    delta = now - created_at
    return format_duration(delta)


# --- Statistics Tracking ---

# A set of all commands that should be tracked in statistics
ALLOWED_STATS_COMMANDS = {
    "!stats",
    "!skip",
    "!refresh",
    "!report",
    "!rules",
    "!about",
    "!info",
    "!whois",
    "!rtimeouts",
    "!roles",
    "!join",
    "!top",
    "!commands",
    "!admin",
    "!admins",
    "!owner",
    "!owners",
    "!timeouts",
    "!times",
    "!rhush",
    "!rsecret",
    "!hush",
    "!secret",
    "!modon",
    "!modoff",
    "!banned",
    "!bans",
    "!clearstats",
    "!start",
    "!pause",
    "!clearwhois",
    "!msearch",
    "!mclear",
    "!mshuffle",
    "!mpauseplay",
    "!mskip",
    "!nowplaying",
    "!np",
    "!queue",
    "!q",
    "!playlist",
    "!volume",
    "!mon",
    "!moff",
    "!help",
    "!music",
    "!purge",
    "!shutdown",
    "!disable",
    "!enable",
    "!disablenotifications",
    "!enablenotifications",
    "!ban",
    "!unbanall",
    "!display",
    "!move",
    "!timer",
    "!timerstop",
}


def record_command_usage(analytics: Dict[str, Any], command_name: str) -> None:
    """
    Increments the global usage count for a specific command.
    """
    if command_name not in ALLOWED_STATS_COMMANDS:
        return
    analytics["command_usage"][command_name] = (
        analytics["command_usage"].get(command_name, 0) + 1
    )


def record_command_usage_by_user(
    analytics: Dict[str, Any], user_id: int, command_name: str
) -> None:
    """
    Increments the usage count for a specific command by a specific user.
    """
    if command_name not in ALLOWED_STATS_COMMANDS:
        return
    if user_id not in analytics["command_usage_by_user"]:
        analytics["command_usage_by_user"][user_id] = {}
    analytics["command_usage_by_user"][user_id][command_name] = (
        analytics["command_usage_by_user"][user_id].get(command_name, 0) + 1
    )


# --- Data Classes ---

@dataclass
class BotConfig:
    """
    Dataclass to hold all configuration variables loaded from config.py.
    Provides type safety and default values.
    """

    # --- Discord IDs ---
    GUILD_ID: int
    COMMAND_CHANNEL_ID: int
    CHAT_CHANNEL_ID: int
    STREAMING_VC_ID: int
    PUNISHMENT_VC_ID: int
    LOG_GC: Optional[int]
    ALT_VC_ID: List[int]
    AUTO_STATS_CHAN: Optional[int]
    MEDIA_ONLY_CHANNEL_ID: Optional[int]

    # --- Omegle / Browser ---
    OMEGLE_VIDEO_URL: str
    EDGE_USER_DATA_DIR: str
    SS_LOCATION: Optional[str]
    EDGE_DRIVER_PATH: Optional[str]

    # --- Permissions ---
    ALLOWED_USERS: Set[int]
    ADMIN_ROLE_NAME: List[str]
    MOVE_ROLE_NAME: List[str]
    MUSIC_ROLES: List[str]
    STATS_EXCLUDED_USERS: Set[int]

    # --- Bot Behavior ---
    JOIN_INVITE_MESSAGE: str
    ENABLE_GLOBAL_HOTKEY: bool
    GLOBAL_HOTKEY_COMBINATION: str
    COMMAND_COOLDOWN: int
    RULES_MESSAGE: str
    INFO_MESSAGES: List[str]
    MOD_MEDIA: bool
    EMPTY_VC_PAUSE: bool
    AUTO_VC_START: bool
    CLICK_CHECKBOX: bool

    # --- Nickname Config ---
    AUTO_NICKNAME: bool
    NICKNAME_TAG: str

    # --- NEW: Auto Relay / Volume Config ---
    AUTO_RELAY: bool
    AUTO_OMEGLE_VOL: bool
    OMEGLE_VOL: int

    # --- Moderation ---
    CAMERA_OFF_ALLOWED_TIME: int
    DEAFEN_ALLOWED_TIME: int  # <--- Ensure this field exists here
    TIMEOUT_DURATION_SECOND_VIOLATION: int
    TIMEOUT_DURATION_THIRD_VIOLATION: int

    # --- Stats Task ---
    AUTO_STATS_HOUR_UTC: int
    AUTO_STATS_MINUTE_UTC: int

    # --- Music ---
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
    def from_config_module(config_module: Any) -> "BotConfig":
        """
        Factory method to create a BotConfig instance from the loaded config.py.
        Provides default values for settings that might be missing.
        """
        default_rules = (
            "## Welcome to the Server!\n"
            "**Rule 1:** Be respectful to others.\n"
            "**Rule 2:** Keep your camera on in the streaming voice channel.\n"
            "**Rule 3:** No hateful or inappropriate content.\n"
        )
        return BotConfig(
            # Discord IDs
            GUILD_ID=getattr(config_module, "GUILD_ID", None),
            COMMAND_CHANNEL_ID=getattr(config_module, "COMMAND_CHANNEL_ID", None),
            CHAT_CHANNEL_ID=getattr(config_module, "CHAT_CHANNEL_ID", None),
            STREAMING_VC_ID=getattr(config_module, "STREAMING_VC_ID", None),
            PUNISHMENT_VC_ID=getattr(config_module, "PUNISHMENT_VC_ID", None),
            LOG_GC=getattr(config_module, "LOG_GC", None),
            ALT_VC_ID=getattr(config_module, "ALT_VC_ID", []),
            AUTO_STATS_CHAN=getattr(config_module, "AUTO_STATS_CHAN", None),
            MEDIA_ONLY_CHANNEL_ID=getattr(
                config_module, "MEDIA_ONLY_CHANNEL_ID", None
            ),
            # Omegle / Browser
            OMEGLE_VIDEO_URL=getattr(config_module, "OMEGLE_VIDEO_URL", None),
            EDGE_USER_DATA_DIR=getattr(config_module, "EDGE_USER_DATA_DIR", None),
            SS_LOCATION=getattr(config_module, "SS_LOCATION", "screenshots"),
            EDGE_DRIVER_PATH=getattr(config_module, "EDGE_DRIVER_PATH", None),
            # Permissions
            ALLOWED_USERS=getattr(config_module, "ALLOWED_USERS", set()),
            ADMIN_ROLE_NAME=getattr(config_module, "ADMIN_ROLE_NAME", []),
            MOVE_ROLE_NAME=getattr(config_module, "MOVE_ROLE_NAME", []),
            MUSIC_ROLES=getattr(config_module, "MUSIC_ROLES", []),
            STATS_EXCLUDED_USERS=getattr(
                config_module, "STATS_EXCLUDED_USERS", set()
            ),
            # Bot Behavior
            JOIN_INVITE_MESSAGE=getattr(config_module, "JOIN_INVITE_MESSAGE", ""),
            ENABLE_GLOBAL_HOTKEY=getattr(config_module, "ENABLE_GLOBAL_HOTKEY", False),
            GLOBAL_HOTKEY_COMBINATION=getattr(
                config_module, "GLOBAL_HOTKEY_COMBINATION", "alt+grave"
            ),
            COMMAND_COOLDOWN=getattr(config_module, "COMMAND_COOLDOWN", 5),
            RULES_MESSAGE=getattr(config_module, "RULES_MESSAGE", default_rules),
            INFO_MESSAGES=getattr(config_module, "INFO_MESSAGES", []),
            MOD_MEDIA=getattr(config_module, "MOD_MEDIA", True),
            EMPTY_VC_PAUSE=getattr(config_module, "EMPTY_VC_PAUSE", True),
            AUTO_VC_START=getattr(config_module, "AUTO_VC_START", False),
            CLICK_CHECKBOX=getattr(config_module, "CLICK_CHECKBOX", True),

            # --- Nickname Config (Validation: Disabled if tag is missing/None) ---
            AUTO_NICKNAME=(
                bool(getattr(config_module, "AUTO_NICKNAME", False))
                if (getattr(config_module, "AUTO_NICKNAME", None) is not None 
                    and getattr(config_module, "NICKNAME_TAG", None) is not None)
                else False
            ),
            NICKNAME_TAG=str(getattr(config_module, "NICKNAME_TAG", "")),
            
            # --- NEW: Load Auto Relay / Volume Config ---
            AUTO_RELAY=getattr(config_module, "AUTO_RELAY", True),
            AUTO_OMEGLE_VOL=getattr(config_module, "AUTO_OMEGLE_VOL", True),
            OMEGLE_VOL=getattr(config_module, "OMEGLE_VOL", 100),

            # Moderation
            CAMERA_OFF_ALLOWED_TIME=getattr(
                config_module, "CAMERA_OFF_ALLOWED_TIME", 30
            ),
            # ---------------------------------------------------------
            # THIS IS THE LINE YOU WERE MISSING IN THE RETURN STATEMENT:
            DEAFEN_ALLOWED_TIME=getattr(config_module, "DEAFEN_ALLOWED_TIME", 300),
            # ---------------------------------------------------------
            TIMEOUT_DURATION_SECOND_VIOLATION=getattr(
                config_module, "TIMEOUT_DURATION_SECOND_VIOLATION", 60
            ),
            TIMEOUT_DURATION_THIRD_VIOLATION=getattr(
                config_module, "TIMEOUT_DURATION_THIRD_VIOLATION", 300
            ),
            # Stats Task
            AUTO_STATS_HOUR_UTC=getattr(config_module, "AUTO_STATS_HOUR_UTC", 0),
            AUTO_STATS_MINUTE_UTC=getattr(config_module, "AUTO_STATS_MINUTE_UTC", 0),
            # Music
            MUSIC_ENABLED=getattr(config_module, "MUSIC_ENABLED", False),
            MUSIC_LOCATION=getattr(config_module, "MUSIC_LOCATION", None),
            MUSIC_BOT_VOLUME=getattr(config_module, "MUSIC_BOT_VOLUME", 0.2),
            MUSIC_MAX_VOLUME=getattr(config_module, "MUSIC_MAX_VOLUME", 1.0),
            MUSIC_SUPPORTED_FORMATS=getattr(
                config_module,
                "MUSIC_SUPPORTED_FORMATS",
                (".mp3", ".flac", ".wav", ".ogg", ".m4a"),
            ),
            MUSIC_DEFAULT_ANNOUNCE_SONGS=getattr(
                config_module, "MUSIC_DEFAULT_ANNOUNCE_SONGS", False
            ),
            NORMALIZE_LOCAL_MUSIC=getattr(
                config_module, "NORMALIZE_LOCAL_MUSIC", True
            ),
            ENABLE_GLOBAL_MSKIP=getattr(config_module, "ENABLE_GLOBAL_MSKIP", False),
            GLOBAL_HOTKEY_MSKIP=getattr(config_module, "GLOBAL_HOTKEY_MSKIP", "grave"),
            ENABLE_GLOBAL_MPAUSE=getattr(config_module, "ENABLE_GLOBAL_MPAUSE", False),
            GLOBAL_HOTKEY_MPAUSE=getattr(config_module, "GLOBAL_HOTKEY_MPAUSE", "grave"),
            ENABLE_GLOBAL_MVOLUP=getattr(config_module, "ENABLE_GLOBAL_MVOLUP", False),
            GLOBAL_HOTKEY_MVOLUP=getattr(config_module, "GLOBAL_HOTKEY_MVOLUP", "]"),
            ENABLE_GLOBAL_MVOLDOWN=getattr(
                config_module, "ENABLE_GLOBAL_MVOLDOWN", False
            ),
            GLOBAL_HOTKEY_MVOLDOWN=getattr(
                config_module, "GLOBAL_HOTKEY_MVOLDOWN", "["
            ),
        )


def build_embed(
    title: str, description: str, color: discord.Color
) -> discord.Embed:
    """Helper function to create a simple Discord embed."""
    return discord.Embed(title=title, description=description, color=color)


async def build_role_update_embed(
    member: discord.Member,
    roles_gained: List[discord.Role],
    roles_lost: List[discord.Role],
) -> discord.Embed:
    """
    Creates a detailed embed for role change announcements.
    """
    user = member
    try:
        # Fetch full user object to get banner
        user = await member.guild.fetch_member(member.id)
    except (discord.NotFound, Exception) as e:
        logger.warning(
            f"Could not fetch full member object for {member.name} during role update: {e}"
        )

    banner_url = user.banner.url if hasattr(user, "banner") and user.banner else None

    embed = discord.Embed(
        title=f"Role Update for {member.name}",
        description=f"{member.mention} had a role change.",
        color=discord.Color.purple(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if banner_url:
        embed.set_image(url=banner_url)

    embed.add_field(
        name="Account Created",
        value=member.created_at.strftime("%m-%d-%Y"),
        inline=True,
    )
    if member.joined_at:
        embed.add_field(
            name="Time in Server", value=get_discord_age(member.joined_at), inline=True
        )
    embed.add_field(name="User ID", value=str(member.id), inline=True)

    if roles_gained:
        embed.add_field(
            name="Roles Gained",
            value=" ".join([role.mention for role in roles_gained]),
            inline=False,
        )
    if roles_lost:
        embed.add_field(
            name="Roles Lost",
            value=" ".join([role.mention for role in roles_lost]),
            inline=False,
        )

    return embed


# --- Type Aliases for BotState ---
# These make the BotState definition cleaner

Cooldowns = Dict[int, Tuple[float, bool]]
MoveCooldowns = Dict[int, float]
ViolationCounts = Dict[int, int]
ActiveTimeouts = Dict[int, Dict[str, Any]]
JoinHistory = List[Tuple[int, str, Optional[str], datetime]]
LeaveHistory = List[Tuple[int, str, Optional[str], datetime, Optional[str]]]
BanHistory = List[Tuple[int, str, Optional[str], datetime, str]]
KickHistory = List[
    Tuple[int, str, Optional[str], datetime, str, Optional[str], Optional[str]]
]
UnbanHistory = List[Tuple[int, str, Optional[str], datetime, str]]
UntimeoutHistory = List[
    Tuple[int, str, Optional[str], datetime, str, Optional[str], Optional[int]]
]
RoleChangeHistory = List[Tuple[int, str, List[str], List[str], datetime]]
AnalyticsData = Dict[str, Union[Dict[str, int], Dict[int, Dict[str, int]], int]]
VcTimeData = Dict[int, Dict[str, Any]]
ActiveVcSessions = Dict[int, float]
Playlists = Dict[str, List[Dict[str, Any]]]
ScreenshotBuffer = List[Tuple[float, bytes]]


# --- Main BotState Class ---

@dataclass
class BotState:
    """
    Dataclass to hold the entire runtime state of the bot.
    This object is what gets serialized to data.json.
    """

    config: BotConfig  # A copy of the loaded config

    # --- Threading Locks ---
    # These locks are crucial to prevent race conditions when
    # multiple async tasks try to modify the same piece of state.
    vc_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    analytics_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    moderation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    music_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    cooldown_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    screenshot_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    music_startup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    menu_repost_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    timeout_wake_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    # --- Cooldowns ---
    cooldowns: Cooldowns = field(default_factory=dict)
    button_cooldowns: Cooldowns = field(default_factory=dict)
    move_command_cooldowns: MoveCooldowns = field(default_factory=dict)
    last_omegle_command_time: float = 0.0

    # --- Moderation State ---
    # Stores active violation countdown tasks.
    # Key: (member_id, violation_type), Value: asyncio.Task
    violation_tasks: Dict[Tuple[int, str], asyncio.Task] = field(default_factory=dict)
    
    # REMOVED: deafen_timers and camera_off_timers (Replaced by violation_tasks)

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
    omegle_disabled_users: Set[int] = field(default_factory=set)
    omegle_enabled: bool = True
    relay_command_sent: bool = False
    last_relay_timestamp: float = 0.0
    leave_buffer: List[dict] = field(default_factory=list, init=False)
    leave_batch_task: Optional[asyncio.Task] = field(default=None, init=False)
    empty_vc_grace_task: Optional[asyncio.Task] = field(default=None, init=False)
    
    # --- History (for !whois) ---
    recent_joins: JoinHistory = field(default_factory=list)
    recent_leaves: LeaveHistory = field(default_factory=list)
    recent_bans: BanHistory = field(default_factory=list)
    recent_kicks: KickHistory = field(default_factory=list)
    recent_unbans: UnbanHistory = field(default_factory=list)
    recent_untimeouts: UntimeoutHistory = field(default_factory=list)
    recent_role_changes: RoleChangeHistory = field(default_factory=list)

    # --- Analytics State ---
    analytics: AnalyticsData = field(
        default_factory=lambda: {
            "command_usage": {},
            "command_usage_by_user": {},
            "violation_events": 0,
        }
    )
    recently_logged_commands: Set[str] = field(default_factory=set)
    last_auto_pause_time: float = 0.0
    vc_time_data: VcTimeData = field(default_factory=dict)
    active_vc_sessions: ActiveVcSessions = field(default_factory=dict)
    
    # --- Timer State ---
    # Stores the active asyncio Task for each user's timer
    active_user_timers: Dict[int, asyncio.Task] = field(default_factory=dict, init=False)

    # --- Music State ---
    music_enabled: bool = True
    all_songs: List[str] = field(default_factory=list)  # All scanned local files
    shuffle_queue: List[str] = field(default_factory=list)  # Shuffled local files
    search_queue: List[Dict[str, Any]] = field(
        default_factory=list
    )  # User-added songs
    active_playlist: List[Dict[str, Any]] = field(
        default_factory=list
    )  # From playlists
    current_song: Optional[Dict[str, Any]] = None
    is_music_playing: bool = False
    is_music_paused: bool = False
    is_processing_song: bool = False  # Lock for when ffmpeg is loading
    music_mode: str = "shuffle"  # 'shuffle', 'alphabetical', 'loop'
    music_volume: float = 0.2
    playlists: Playlists = field(default_factory=dict)
    announcement_context: Optional[Any] = None
    play_next_override: bool = False  # For !q jumping
    stop_after_clear: bool = False  # For !mclear

    # --- Browser/Ban State ---
    window_size: Optional[Dict[str, int]] = field(default=None)
    window_position: Optional[Dict[str, int]] = field(default=None)
    is_banned: bool = False
    active_votes: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    last_vc_connect_fail_time: float = 0.0
    ban_message_id: Optional[int] = None
    ban_screenshots: ScreenshotBuffer = field(default_factory=list, init=False)
    
    # Track the grace period task for music disconnection
    music_disconnect_task: Optional[asyncio.Task] = field(default=None, init=False)

    # --- Message IDs (for editing) ---
    music_menu_message_id: Optional[int] = None
    times_report_message_id: Optional[int] = None
    timeouts_report_message_id: Optional[int] = None

    def __post_init__(self):
        """Called after the dataclass is initialized."""
        self.timeout_wake_event = asyncio.Event()
        if self.config:
            self.music_volume = self.config.MUSIC_BOT_VOLUME
            self.music_enabled = self.config.MUSIC_ENABLED

    def to_dict(
        self,
        guild: Optional[discord.Guild],
        active_vc_sessions_to_save: dict,
        current_time: float,
    ) -> dict:
        """
        Serializes the BotState into a dictionary suitable for JSON.
        """
        # --- Handle Active VC Sessions ---
        # We must "flush" active sessions to the main time data before saving
        vc_data_to_save = {
            user_id: data.copy() for user_id, data in self.vc_time_data.items()
        }
        for user_id, session_start in active_vc_sessions_to_save.items():
            session_duration = current_time - session_start
            if user_id not in vc_data_to_save:
                # If user joined and never left, they won't be in vc_time_data yet
                member = guild.get_member(user_id) if guild else None
                username = member.name if member else "Unknown"
                display_name = member.display_name if member else "Unknown"
                vc_data_to_save[user_id] = {
                    "total_time": 0,
                    "sessions": [],
                    "username": username,
                    "display_name": display_name,
                }
            # Add this active session as a completed session
            vc_data_to_save[user_id]["sessions"].append(
                {
                    "start": session_start,
                    "end": current_time,
                    "duration": session_duration,
                    "vc_name": "Streaming VC",
                }
            )
            vc_data_to_save[user_id]["total_time"] += session_duration

        def clean_song_dict(song_dict: Optional[Dict]) -> Optional[Dict]:
            """Removes non-serializable 'ctx' from song dicts."""
            if not song_dict:
                return None
            return {key: value for key, value in song_dict.items() if key != "ctx"}

        # --- Return Serializable Dictionary ---
        return {
            "analytics": self.analytics,
            "omegle_enabled": self.omegle_enabled,
            "relay_command_sent": self.relay_command_sent,
            "users_received_rules": list(self.users_received_rules),
            "user_violations": self.user_violations,
            "active_timeouts": self.active_timeouts,
            "notifications_enabled": self.notifications_enabled,
            "move_command_cooldowns": self.move_command_cooldowns,
            "recent_joins": [
                {
                    "id": e[0],
                    "name": e[1],
                    "display_name": e[2],
                    "timestamp": e[3].isoformat(),
                }
                for e in self.recent_joins
            ],
            "recent_leaves": [
                {
                    "id": e[0],
                    "name": e[1],
                    "display_name": e[2],
                    "timestamp": e[3].isoformat(),
                    "roles": e[4],
                }
                for e in self.recent_leaves
            ],
            "recent_role_changes": [
                {
                    "id": e[0],
                    "name": e[1],
                    "gained": e[2],
                    "lost": e[3],
                    "timestamp": e[4].isoformat(),
                }
                for e in self.recent_role_changes
            ],
            "users_with_dms_disabled": list(self.users_with_dms_disabled),
            "recent_bans": [
                {
                    "id": e[0],
                    "name": e[1],
                    "display_name": e[2],
                    "timestamp": e[3].isoformat(),
                    "reason": e[4],
                }
                for e in self.recent_bans
            ],
            "recent_kicks": [
                {
                    "id": e[0],
                    "name": e[1],
                    "display_name": e[2],
                    "timestamp": e[3].isoformat(),
                    "reason": e[4],
                    "moderator": e[5],
                    "roles": e[6],
                }
                for e in self.recent_kicks
            ],
            "recent_unbans": [
                {
                    "id": e[0],
                    "name": e[1],
                    "display_name": e[2],
                    "timestamp": e[3].isoformat(),
                    "moderator": e[4],
                }
                for e in self.recent_unbans
            ],
            "recent_untimeouts": [
                {
                    "id": e[0],
                    "name": e[1],
                    "display_name": e[2],
                    "timestamp": e[3].isoformat(),
                    "reason": e[4],
                    "moderator_name": e[5],
                    "moderator_id": e[6],
                }
                for e in self.recent_untimeouts
            ],
            "omegle_disabled_users": list(self.omegle_disabled_users),
            "recent_kick_timestamps": {
                k: v.isoformat() for k, v in self.recent_kick_timestamps.items()
            },
            "vc_time_data": {
                str(user_id): data for user_id, data in vc_data_to_save.items()
            },
            "active_vc_sessions": {},  # Active sessions are flushed, not saved
            "music_enabled": self.music_enabled,
            "music_mode": self.music_mode,
            "search_queue": [clean_song_dict(s) for s in self.search_queue],
            "active_playlist": [clean_song_dict(s) for s in self.active_playlist],
            "current_song": clean_song_dict(self.current_song),
            "music_volume": self.music_volume,
            "playlists": {
                p_name: [clean_song_dict(s) for s in songs]
                for p_name, songs in self.playlists.items()
            },
            "window_size": self.window_size,
            "window_position": self.window_position,
            "is_banned": self.is_banned,
            "ban_message_id": self.ban_message_id,
            "music_menu_message_id": self.music_menu_message_id,
            "times_report_message_id": self.times_report_message_id,
            "timeouts_report_message_id": self.timeouts_report_message_id,
            "active_votes": self.active_votes,
            "vc_moderation_active": self.vc_moderation_active,
            "last_vc_connect_fail_time": self.last_vc_connect_fail_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], config: BotConfig) -> "BotState":
        """
        Factory method to create a BotState instance from a loaded JSON dict.
        """
        state = cls(config=config)

        # --- Analytics ---
        analytics = data.get(
            "analytics",
            {
                "command_usage": {},
                "command_usage_by_user": {},
                "violation_events": 0,
            },
        )
        if "command_usage_by_user" in analytics:
            # Convert user ID keys from str back to int
            analytics["command_usage_by_user"] = {
                int(k): v
                for k, v in analytics.get("command_usage_by_user", {}).items()
            }
        state.analytics = analytics

        # --- Moderation ---
        state.user_violations = {
            int(k): v for k, v in data.get("user_violations", {}).items()
        }
        state.active_timeouts = {
            int(k): v for k, v in data.get("active_timeouts", {}).items()
        }
        state.notifications_enabled = data.get("notifications_enabled", True)
        state.users_received_rules = set(data.get("users_received_rules", []))
        state.users_with_dms_disabled = set(data.get("users_with_dms_disabled", []))
        state.omegle_disabled_users = set(data.get("omegle_disabled_users", []))
        state.omegle_enabled = data.get("omegle_enabled", True)
        state.relay_command_sent = data.get("relay_command_sent", False)
        state.move_command_cooldowns = {
            int(k): v for k, v in data.get("move_command_cooldowns", {}).items()
        }

        # --- History (with timestamp conversion) ---
        state.recent_joins = [
            (e["id"], e["name"], e["display_name"], datetime.fromisoformat(e["timestamp"]))
            for e in data.get("recent_joins", [])
        ]
        state.recent_leaves = [
            (
                e["id"],
                e["name"],
                e["display_name"],
                datetime.fromisoformat(e["timestamp"]),
                e["roles"],
            )
            for e in data.get("recent_leaves", [])
        ]
        state.recent_role_changes = [
            (
                e["id"],
                e["name"],
                e["gained"],
                e["lost"],
                datetime.fromisoformat(e["timestamp"]),
            )
            for e in data.get("recent_role_changes", [])
        ]
        state.recent_bans = [
            (
                e["id"],
                e["name"],
                e["display_name"],
                datetime.fromisoformat(e["timestamp"]),
                e["reason"],
            )
            for e in data.get("recent_bans", [])
        ]
        state.recent_kicks = [
            (
                e["id"],
                e["name"],
                e["display_name"],
                datetime.fromisoformat(e["timestamp"]),
                e["reason"],
                e["moderator"],
                e["roles"],
            )
            for e in data.get("recent_kicks", [])
        ]
        state.recent_unbans = [
            (
                e["id"],
                e["name"],
                e["display_name"],
                datetime.fromisoformat(e["timestamp"]),
                e["moderator"],
            )
            for e in data.get("recent_unbans", [])
        ]
        state.recent_untimeouts = [
            (
                e["id"],
                e["name"],
                e["display_name"],
                datetime.fromisoformat(e["timestamp"]),
                e["reason"],
                e.get("moderator_name"),
                e.get("moderator_id"),
            )
            for e in data.get("recent_untimeouts", [])
        ]
        state.recent_kick_timestamps = {
            int(k): datetime.fromisoformat(v)
            for k, v in data.get("recent_kick_timestamps", {}).items()
        }

        # --- VC Time & Music ---
        state.vc_time_data = {
            int(k): v for k, v in data.get("vc_time_data", {}).items()
        }
        state.active_vc_sessions = (
            {}
        )  # This is always reset on load
        state.music_enabled = data.get(
            "music_enabled", config.MUSIC_ENABLED if config else True
        )
        state.music_mode = data.get("music_mode", "shuffle")
        state.search_queue = data.get("search_queue", [])
        state.active_playlist = data.get("active_playlist", [])
        state.current_song = data.get("current_song", None)
        state.music_volume = data.get(
            "music_volume", config.MUSIC_BOT_VOLUME if config else 0.2
        )
        state.playlists = data.get("playlists", {})

        # --- Other State ---
        state.window_size = data.get("window_size", None)
        state.window_position = data.get("window_position", None)
        state.is_banned = data.get("is_banned", False)
        # Ensure keys are integers (Message IDs)
        raw_votes = data.get("active_votes", {})
        state.active_votes = {int(k): v for k, v in raw_votes.items()}
        state.ban_message_id = data.get("ban_message_id", None)
        state.ban_message_id = data.get("ban_message_id", None)
        state.music_menu_message_id = data.get("music_menu_message_id", None)
        state.times_report_message_id = data.get("times_report_message_id", None)
        state.timeouts_report_message_id = data.get("timeouts_report_message_id", None) # <-- ADDED
        state.vc_moderation_active = data.get("vc_moderation_active", True)
        state.last_vc_connect_fail_time = data.get(
            "last_vc_connect_fail_time", 0.0
        )

        return state

    async def check_and_log_command(self, log_id: str) -> bool:
        """
        Atomically checks if a command has been logged recently and logs it.
        Used for deduplication.

        Returns:
            True if this is a new log, False if it was already logged.
        """
        async with self.cooldown_lock:
            if log_id in self.recently_logged_commands:
                return False  # Already logged
            self.recently_logged_commands.add(log_id)
            return True  # New log

    async def clean_old_entries(self) -> None:
        """
        Task to periodically clean up old data from state to prevent
        memory leaks and keep the data.json file size reasonable.
        """
        current_time = time.time()
        now = datetime.now(timezone.utc)
        seven_days_ago_dt = now - timedelta(days=7)

        # --- Clean Cooldowns and Timers ---
        async with self.vc_lock, self.analytics_lock, self.moderation_lock, self.music_lock:
            async with self.cooldown_lock:
                self.cooldowns = {
                    k: v
                    for k, v in self.cooldowns.items()
                    if current_time - v[0] < self.config.COMMAND_COOLDOWN * 2
                }
                self.button_cooldowns = {
                    k: v
                    for k, v in self.button_cooldowns.items()
                    if current_time - v[0] < self.config.COMMAND_COOLDOWN * 2
                }
                self.move_command_cooldowns = {
                    k: v
                    for k, v in self.move_command_cooldowns.items()
                    if current_time - v < 3900  # ~1 hour
                }
            
            self.active_timeouts = {
                k: v
                for k, v in self.active_timeouts.items()
                if v.get("timeout_end", float("inf")) > current_time
            }
            self.recent_kick_timestamps = {
                k: v
                for k, v in self.recent_kick_timestamps.items()
                if now - v < timedelta(days=7)
            }

            # --- Clean Large Sets ---
            for dataset in [
                self.failed_dm_users,
                self.users_with_dms_disabled,
                self.users_received_rules,
            ]:
                if len(dataset) > 1000:
                    dataset.clear()

            if len(self.recently_banned_ids) > 200:
                self.recently_banned_ids.clear()

            # --- Clean VC Time Data (keep last 7 days) ---
            seven_days_ago_ts = current_time - 7 * 24 * 3600
            self.vc_time_data = {
                user_id: {
                    **data,
                    "sessions": [
                        s
                        for s in data.get("sessions", [])
                        if s.get("end", 0) > seven_days_ago_ts
                    ],
                }
                for user_id, data in self.vc_time_data.items()
                if any(
                    (s.get("end", 0) > seven_days_ago_ts for s in data.get("sessions", []))
                )
            }

            # --- Clean Analytics Data (limit to top 1000) ---
            if (
                isinstance(self.analytics.get("command_usage_by_user"), dict)
                and len(self.analytics["command_usage_by_user"]) > 1000
            ):
                user_usage_sorted = sorted(
                    self.analytics["command_usage_by_user"].items(),
                    key=lambda x: sum(x[1].values()),
                    reverse=True,
                )
                self.analytics["command_usage_by_user"] = dict(user_usage_sorted[:1000])

            if (
                isinstance(self.analytics.get("command_usage"), dict)
                and len(self.analytics["command_usage"]) > 100
            ):
                commands_sorted = sorted(
                    self.analytics["command_usage"].items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
                self.analytics["command_usage"] = dict(commands_sorted[:100])

            # --- Clean History Lists (keep last 7 days, max 200 entries) ---
            list_specs = {
                "recent_joins": (3, 200),
                "recent_leaves": (3, 200),
                "recent_bans": (3, 200),
                "recent_kicks": (3, 200),
                "recent_unbans": (3, 200),
                "recent_untimeouts": (3, 200),
                "recent_role_changes": (4, 200),
            }
            for list_name, (time_idx, max_entries) in list_specs.items():
                lst = getattr(self, list_name)
                cleaned = [
                    entry
                    for entry in lst
                    if len(entry) > time_idx
                    and isinstance(entry[time_idx], datetime)
                    and (entry[time_idx] > seven_days_ago_dt)
                ][-max_entries:]
                setattr(self, list_name, cleaned)

        # --- Clean Command Log (separate lock) ---
        if len(self.recently_logged_commands) > 5000:
            async with self.cooldown_lock:
                self.recently_logged_commands.clear()