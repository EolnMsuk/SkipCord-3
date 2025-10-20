# config.py
# This is a template configuration file for the SkipCord-2 bot.
# Replace the placeholder values (like 0, None, or "path/to/...") with your actual server and user information.

# --- ⚙️ DISCORD SERVER CONFIGURATION ⚙️ ---
# These IDs are ESSENTIAL. The bot will not work without them.
# How to get IDs: In Discord, go to User Settings > Advanced > enable Developer Mode.
# Then, right-click on your server icon or a channel name and select "Copy ID".

# (Required)
GUILD_ID = 0           # PASTE YOUR SERVER'S ID HERE.
CHAT_CHANNEL_ID = 0    # PASTE the ID of your main chat channel. The bot will post announcements here (joins, bans, etc.).
COMMAND_CHANNEL_ID = 0 # PASTE the ID of the text channel where users will type commands like !skip.
STREAMING_VC_ID = 0    # PASTE the ID of the voice channel where the Omegle stream happens and camera rules are enforced.
PUNISHMENT_VC_ID = 0   # PASTE the ID of a "jail" voice channel. Users are moved here for their first camera violation.

# (Optional)
ALT_VC_ID = None             # (Optional) PASTE the ID of a SECOND voice channel where camera rules should also be enforced. Set to None if you don't have one.
AUTO_STATS_CHAN = None       # (Optional) PASTE the ID of a channel for daily automatic VC time reports. Set to None to have it post in CHAT_CHANNEL.
MEDIA_ONLY_CHANNEL_ID = None # (Optional) PASTE the ID of a channel where the bot will delete non-media messages. Set to None to disable.

# --- 👑 SERVER OWNER PERMISSIONS 👑 ---
# Controls who can use the most powerful bot commands (e.g., !shutdown, !ban).
# Add ONLY Server Owner USER ID (unless you know what you are doing).
# To get a user ID, right-click their name and "Copy ID".
# Example: ALLOWED_USERS = {987654321098765432} or {98765432109776546, 123456788932464}
ALLOWED_USERS = {123456789012345678}

# --- 🛡️ ROLE CONFIGURATION 🛡️ ---
# List the EXACT names of roles that should have semi-admin-level command access.
# Case-sensitive! Example: ADMIN_ROLE_NAME = ["Moderator", "Server Admin"]
ADMIN_ROLE_NAME = ["Admin", "Moderator"]

# A set of user IDs to completely exclude from all statistical tracking (VC time, command usage).
# NOTE: All bots are automatically excluded by the code. This setting is for excluding the omegle host.
STATS_EXCLUDED_USERS = {}

# --- ⚙️ GENERAL BOT SETTINGS ⚙️ ---
# If True, the bot will delete any message in the MEDIA_ONLY_CHANNEL_ID that isn't a picture, video, or link.
MOD_MEDIA = False

# --- 🌐 BROWSER AUTOMATION (SELENIUM) 🌐 ---
# These settings control the web browser that runs the Omegle stream.
# The URL the bot will open. Change only if you use a different Omegle mirror.
OMEGLE_VIDEO_URL = "https://uhmegle.com/video"

# If True, the bot will automatically pause/refresh the Omegle stream when the last user with a camera leaves the STREAMING_VC_ID.
EMPTY_VC_PAUSE = True

# If True, the bot will automatically start/skip the Omegle stream when the first user with a camera joins the STREAMING_VC_ID.
AUTO_VC_START = False

# Location of folder for screenshots (reports and bans)
SS_LOCATION = "C:\\Users\\username\\Desktop\\screenshots"

# The FULL path to your Microsoft Edge "Profile" folder. This is crucial for the 
# browser to remember settings, stay logged into sites, and appear less like a bot.
#
# HOW TO FIND IT:
#  1. Open Edge and go to `edge://version/`
#  2. Copy the entire "Profile path". 
#     - It will look something like: 
#       C:\Users\YourUser\AppData\Local\Microsoft\Edge\User Data\Profile 3
#     - If you don't use profiles, it might end in `\Default` instead of `\Profile 3`
#
# HOW TO FORMAT IT:
#  - Replace every single backslash `\` with a double backslash `\\`.
#  - Enclose the entire path in quotes.
#
# EXAMPLES:
# EDGE_USER_DATA_DIR = "C:\\Users\\YourUser\\AppData\\Local\\Microsoft\\Edge\\User Data\\Profile 3"
# EDGE_USER_DATA_DIR = "C:\\Users\\YourUser\\AppData\\Local\\Microsoft\\Edge\\User Data\\Default"
#
# TIP FOR A DEDICATED BOT PROFILE:
# You can enter a path to a profile that doesn't exist yet, like "Profile 10".
# The bot will create a new, clean profile folder when it starts. You can then
# open Edge with that profile to customize it (e.g., install extensions like Chromegle).
EDGE_USER_DATA_DIR = "C:\\Users\\YourUser\\AppData\\Local\\Microsoft\\Edge\\User Data\\Profile 3"

# --- ⏰ AUTOMATED STATS SCHEDULE (UTC TIME) ⏰ ---
# This determines when the daily stats report is posted and stats are reset.
# Uses 24-hour format. You can use a UTC time converter online to find the right time for your timezone.

AUTO_STATS_HOUR_UTC = 4              # The hour (0-23) in UTC. Example: 14 for 2 PM UTC.
AUTO_STATS_MINUTE_UTC = 0            # The minute (0-59) in UTC.

# --- ⌨️ GLOBAL HOTKEY ⌨️ ---
# Allows you to trigger a !skip from anywhere on your computer.
ENABLE_GLOBAL_HOTKEY = True               # Set to True to enable, False to disable.

# The key combination. See the `keyboard` library documentation for format.
# Examples: "alt+s", "ctrl+shift+f12", "alt+`"
GLOBAL_HOTKEY_COMBINATION = "alt+grave"

# --- 👮 VOICE CHANNEL MODERATION 👮 ---
# The global cooldown in seconds between using Omegle commands (!skip, !refresh).
COMMAND_COOLDOWN = 5

# How long (in seconds) a user can have their camera off in a moderated VC before punishment.
CAMERA_OFF_ALLOWED_TIME = 30

# The timeout duration (in seconds) for a user's SECOND camera-off violation.
TIMEOUT_DURATION_SECOND_VIOLATION = 60      # 1 minute

# The timeout duration (in seconds) for a user's THIRD (and any subsequent) violation.
TIMEOUT_DURATION_THIRD_VIOLATION = 300      # 5 minutes

# --- 📝 CUSTOM MESSAGES 📝 ---
# The message DMed to users with an ADMIN_ROLE_NAME when the `!join` command is used.
JOIN_INVITE_MESSAGE = (
    "The stream is starting! Please join the main voice channel."
)

# The message for the `!rules` command and DMed to new users. Supports Discord markdown.
RULES_MESSAGE = """
## Welcome to the Server!

**Rule 1:** Be respectful to others.
**Rule 2:** Keep your camera on in the streaming voice channel.
**Rule 3:** No hateful or inappropriate content.
"""

# A list of messages for the `!info` command. Each string is sent as a separate message.
INFO_MESSAGES = [
"""
## Server Information

This is a server for our 24/7 group Omegle call, powered by the SkipCord-2 bot! 
Please read the #rules channel for a full list of our community guidelines.

**Key Rules:**
- Cameras must be on while in the Streaming VC.
- Be respectful and keep conversations civil.
- Do not go AFK for extended periods.

Enjoy your stay!
"""
]

# (Optional) You can uncomment and customize the keys used for the skip command's browser automation.
# The default is ["Escape", "Escape"].
SKIP_COMMAND_KEY = None

# Setting this to False will disable any and all music bot functionalities of this bot.
MUSIC_ENABLED = True

# Set it to True to normalize the volume of local music files, making them consistent with online streams.
# Set it to False to play local files at their original, un-normalized volume.
NORMALIZE_LOCAL_MUSIC = False

# Optional: Set to True if you want the bot to announce every new song in the chat. Default is False.
MUSIC_DEFAULT_ANNOUNCE_SONGS = False

# --- MUSIC BOT CONFIGURATION ---
MUSIC_LOCATION = "C:/path/to/your/music/folder"             # (Optional) The FULL path to your music directory. Set to None to disable.
MUSIC_BOT_VOLUME = 0.2                                     # The default volume for the music bot (0.0 to 2.0).

# Optional: The highest volume the !volume command can be set to (e.g., 1.0 = 100%). Default is 1.0.
MUSIC_MAX_VOLUME = 1.0

# Optional: The audio file formats the bot will scan for in your local music directory.
MUSIC_SUPPORTED_FORMATS = ('.mp3', '.flac', '.wav', '.ogg', '.m4a')

# --- MUSIC HOTKEY CONFIGURATION ---
# Configure global hotkeys on the host machine to trigger music commands.

ENABLE_GLOBAL_MSKIP = False             # Set to True to enable the !mskip hotkey, False to disable it.
GLOBAL_HOTKEY_MSKIP = "end"             # The key to trigger the !mskip. Uses the `keyboard` library format.

ENABLE_GLOBAL_MPAUSE = False            # Set to True to enable the !mpause hotkey, False to disable it.
GLOBAL_HOTKEY_MPAUSE = "page down"      # The key to trigger the !mpause. Uses the `keyboard` library format.

ENABLE_GLOBAL_MVOLDOWN = False          # Set to True to enable the !mvoldown hotkey, False to disable it.
GLOBAL_HOTKEY_MVOLDOWN = "["            # The key to trigger the !mvoldown. Uses the `keyboard` library format.

ENABLE_GLOBAL_MVOLUP = False            # Set to True to enable the !mvolup hotkey, False to disable it.

GLOBAL_HOTKEY_MVOLUP = "]"              # The key to trigger the !mvolup. Uses the `keyboard` library format.





