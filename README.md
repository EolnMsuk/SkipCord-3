# SkipCord-2: Omegle Streaming & Music Bot for Discord

SkipCord-2 is a powerful, fully modular Discord bot designed for streamers who use Omegle or similar platforms. It allows streamers to share their experience in a Discord voice channel, giving everyone control via an interactive button menu. The bot includes advanced moderation, detailed logging, automated rule enforcement, and a complete music bot feature set, all built on a fully asynchronous architecture for rock-solid performance.

## Key Features

### **Stream Controls & User Experience**

* **Interactive Button Menus**: Users can control the stream (`!skip`, `!info`) and music (`!mskip`, `!mpauseplay`) with clean, persistent button menus that refresh periodically.
* **VC Time Tracking**: Tracks the cumulative time users spend in moderated voice channels, with daily leaderboards via the `!times` command.
* **Auto-Pause**: Automatically refreshes (pauses) the stream when the last user with their camera on leaves the VC or turns their camera off.
* **Global Hotkeys**: Configure keyboard shortcuts to trigger commands like `!skip`, `!mskip`, `!mpauseplay`, and volume controls from anywhere on the host machine.
* **State Persistence**: All critical data (stats, violations, timeouts, event history, playlists) is saved to a `data.json` file and reloaded on startup, ensuring no data is lost on restart or crash.

### **Advanced Moderation & Automation**

* **Camera Enforcement**:
    * Non-admin users without cameras in a moderated VC are automatically muted/deafened.
    * **1st Violation**: User is moved to a designated punishment VC.
    * **2nd Violation**: User receives a short timeout.
    * **3rd+ Violations**: User receives a longer timeout.
* **Media-Only Channel**: Automatically deletes any messages in a designated channel that do not contain an image, video, link, or other media attachment.
* **Daily Auto-Stats**: Posts a full analytics report (VC time, command usage, etc.) daily at a configured UTC time, then automatically clears all statistics for the next day.
* **Comprehensive Logging**: Uses `loguru` for detailed, color-coded logs of all commands, moderation actions, and server events, saved to `bot.log`.

### **Full Music Bot Integration** 🎵

* **YouTube & Local File Support**: Play songs from YouTube URLs, search terms, or a local music library.
* **Interactive Queue**: View the song queue with `!q` and jump to any song using a dropdown menu.
* **Playlist System**: Save the current queue as a named playlist, then load, list, or delete playlists.
* **Multiple Playback Modes**: Cycle between Shuffle, Alphabetical (for local files), and Loop modes.
* **Automatic Management**: The bot automatically joins the VC when users with cameras are present and leaves when it's empty to save resources.

### **Comprehensive Event Notifications**

The bot ensures administrators are always informed with a robust, event-driven notification system. Using rich, detailed embeds sent to a designated chat channel, it provides real-time updates for all significant server activities.

* **Member Activity**: Joins, Leaves (batched for mass departures), Kicks, Bans, and Unbans.
* **Moderation Actions**: Timeouts Added/Removed and Role Changes.
* **Bot & Stream Status**: Bot Online, Stream Auto-Pause, and Browser Health notifications.

---

## Command List

### 👤 User Commands

*(Requires being in the Streaming VC with camera on)*

* `!skip` / `!start`: Skips the current Omegle user.
* `!refresh` / `!pause`: Refreshes the Omegle page.
* `!info` / `!about`: Shows server information and rules.
* `!rules`: Displays the server rules.
* `!times`: Shows the top 10 most active VC users.
* `!m <query>`: Searches for a song/URL to add to the queue.
* `!q` / `!queue`: Displays the interactive song queue.
* `!np` / `!nowplaying`: Shows the currently playing song.
* `!mskip`: Skips the current song.
* `!mpp` / `!mpauseplay`: Toggles music play/pause.
* `!mclear`: Clears all songs from the search queue.
* `!mshuffle`: Cycles music mode (Shuffle -> Alphabetical -> Loop).
* `!vol <0-100>`: Sets the music volume.
* `!playlist <save|load|list|delete> [name]`: Manages playlists.

### 🛡️ Admin Commands

*(Requires Admin Role or being an Allowed User + Camera On)*

* `!help`: Sends the interactive help menu.
* `!music`: Sends the interactive music control menu.
* `!bans` / `!banned`: Lists all banned users.
* `!timeouts`: Shows currently timed-out users.
* `!rtimeouts`: Removes all active timeouts.
* `!display <user>`: Shows a detailed profile for a user.
* `!commands`: Shows this list of all commands.
* `!mon`: Enables music features and connects the bot.
* `!moff`: Disables music features and disconnects the bot.

### 👑 Owner Commands (Allowed Users Only)

*(No channel or VC restrictions)*

* `!purge <count>`: Deletes a specified number of messages.
* `!shutdown`: Safely shuts down the bot.
* `!hush`: Server-mutes all non-admin users.
* `!rhush`: Removes server-mutes.
* `!secret`: Server-mutes and deafens all non-admin users.
* `!rsecret`: Removes server-mutes and deafens.
* `!modon` / `!modoff`: Toggles automated VC moderation.
* `!disablenotifications` / `!enablenotifications`: Toggles event notifications.
* `!disable <user>`: Prevents a user from using commands.
* `!enable <user>`: Re-enables a disabled user.
* `!ban <user>`: Interactively bans user(s).
* `!unban <user_id>`: Interactively unbans a user by ID.
* `!unbanall`: Interactively unbans all users.
* `!top`: Lists the top 10 oldest server members and Discord accounts.
* `!roles`: Lists all server roles and their members.
* `!admin` / `!owner`: Lists configured owners and admins.
* `!whois`: Shows a 24-hour report of all server activity.
* `!stats`: Shows a detailed analytics report.
* `!join`: DMs a join invite to all users with an admin role.
* `!clearstats`: Clears all statistical data.
* `!clearwhois`: Clears all historical event data.

---

## Setup & Configuration

### 1) Prerequisites

* **Microsoft Edge**: Ensure the Edge browser is installed and up-to-date.
* **Python 3.9+**: Install from [python.org](https://www.python.org/downloads/). Make sure to check "Add Python to PATH" during installation.
* **FFmpeg**: Required for music playback. Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add it to your system's PATH.
* **Dependencies**: Run `cmd.exe` as Admin > Paste then hit Enter:
    ```
    pip install discord.py python-dotenv selenium loguru keyboard mutagen yt-dlp
    ```

### 2) Create a Discord Bot

1.  Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application/bot.
2.  Navigate to the "Bot" tab and enable the following **Privileged Gateway Intents**:
    * **Message Content Intent**
    * **Server Members Intent**
3.  Copy your **Bot Token** and keep it secret.
4.  Go to the "OAuth2" -> "URL Generator" tab. Select the `bot` and `applications.commands` scopes.
5.  In the "Bot Permissions" section below, select `Administrator`.
6.  Copy the generated URL and use it to invite your bot to your server.

### 3) File Setup

1.  Create a folder for your bot and place all the python files (`bot.py`, `helper.py`, `omegle.py`, `tools.py`), your `config.py`, and `requirements.txt` inside.
2.  Create a new file named `.env` in the same folder.
3.  Add the following line to the `.env` file, replacing `your_token_here` with your actual bot token:
    `BOT_TOKEN=your_token_here`

### 4) Configure `config.py`

Open `config.py` and replace the placeholder values with your server's specific IDs and settings. You can get these IDs by enabling Developer Mode in Discord, then right-clicking on a server, channel, or user and selecting "Copy ID".

```python
# --- REQUIRED SETTINGS ---
GUILD_ID = 123456789012345678                # Your Discord Server ID
COMMAND_CHANNEL_ID = 123456789012345678      # Channel for bot commands and menus
CHAT_CHANNEL_ID = 123456789012345678         # Channel for join/leave/ban notifications
STREAMING_VC_ID = 123456789012345678         # Main streaming/music voice channel
PUNISHMENT_VC_ID = 123456789012345678        # VC where users are moved for a first violation
OMEGLE_VIDEO_URL = "[https://example.com](https://example.com)"     # URL for the streaming website
# Find it by going to edge://version/ in your browser and copying the "Profile path"
EDGE_USER_DATA_DIR = "C:/Users/YourUser/AppData/Local/Microsoft/Edge/User Data"

# --- PERMISSIONS ---
ALLOWED_USERS = {123456789012345678, 987654321098765432} # Full bot access
ADMIN_ROLE_NAME = ["Admin", "Moderator"] # Roles that can use admin commands

# --- OPTIONAL FEATURES ---
# (Set to None to disable)
ALT_VC_ID = None                     # A second voice channel to moderate
AUTO_STATS_CHAN = 123456789012345678 # Channel for daily auto-stats reports
MEDIA_ONLY_CHANNEL_ID = None         # Channel where only media is allowed
MOD_MEDIA = True                     # Enable/disable media-only channel moderation
EMPTY_VC_PAUSE = True                # Auto-pauses stream when VC is empty
STATS_EXCLUDED_USERS = {123456789012345678} # User IDs to exclude from stats

# --- TIMING & MESSAGES ---
AUTO_STATS_HOUR_UTC = 5              # UTC hour for auto-stats (0-23)
CAMERA_OFF_ALLOWED_TIME = 30         # Seconds a user can have camera off before punishment
TIMEOUT_DURATION_SECOND_VIOLATION = 60  # Seconds for 2nd violation timeout
TIMEOUT_DURATION_THIRD_VIOLATION = 300 # Seconds for 3rd+ violation timeout
INFO_MESSAGES = ["Welcome! Rules: Camera on in VC."]
JOIN_INVITE_MESSAGE = "An admin has requested your presence in the stream! Join here: <#CHANNEL_ID>"

# --- MUSIC BOT SETTINGS ---
MUSIC_ENABLED = True                 # Master toggle for all music features
# Path to a folder with local music files (e.g., "C:/Users/YourUser/Music")
MUSIC_LOCATION = None
MUSIC_BOT_VOLUME = 0.2               # Default volume (0.0 to 1.0)
MUSIC_MAX_VOLUME = 1.0               # Maximum volume allowed for the !vol command

# --- GLOBAL HOTKEYS ---
ENABLE_GLOBAL_HOTKEY = True
GLOBAL_HOTKEY_COMBINATION = 'alt+grave'  # Hotkey for !skip
ENABLE_GLOBAL_MSKIP = True
GLOBAL_HOTKEY_MSKIP = '`'              # Hotkey for !mskip
ENABLE_GLOBAL_MPAUSE = True
GLOBAL_HOTKEY_MPAUSE = 'page down'     # Hotkey for !mpauseplay
ENABLE_GLOBAL_MVOLUP = True
GLOBAL_HOTKEY_MVOLUP = ']'             # Hotkey for volume up
ENABLE_GLOBAL_MVOLDOWN = True
GLOBAL_HOTKEY_MVOLDOWN = '['           # Hotkey for volume down
```

### 5) Running the Bot

1.  **Important**: Close all currently running instances of the Microsoft Edge browser.
2.  Open your command prompt, navigate to the bot's folder (`cd path/to/your/bot`), and run the bot using:
    `python bot.py`
3.  The bot will launch Edge, navigate to the configured URL, and start all monitoring systems.
4.  **Troubleshooting**:
    * If the bot fails with a token error, ensure your `.env` file is correct and in the same folder.
    * If Edge doesn't launch, double-check that the `EDGE_USER_DATA_DIR` path in `config.py` is correct.
    * If you get a "webdriver" error, ensure your Edge browser is fully updated.
    * If music doesn't play, ensure **FFmpeg** is correctly installed and in your system's PATH.
    * Check the `bot.log` file for any specific error messages.
