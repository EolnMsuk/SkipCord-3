# SkipCord-3: Omegle & Music Bot for Discord

SkipCord-3 is a powerful, multi-purpose Discord bot designed for communities. It combines the Omegle stream-sharing features of its predecessor with a brand new, fully-featured music bot. It allows you to host a shared Omegle stream that your community can control, while also providing high-quality music playback from local files or online sources.

The bot includes advanced moderation features, detailed logging, automated enforcement of streaming rules, and a fully asynchronous architecture for rock-solid performance.

[Key Features](https://www.google.com/search?q=%23key-features) | [Bot Commands](https://www.google.com/search?q=%23command-list) | [How to Setup](https://www.google.com/search?q=%23setup--configuration)

## Key Features

### **🌐 Omegle Stream Controls**

* **Interactive Button Menu**: Users can control the Omegle stream (`!skip`, `!refresh`, etc.) with a clean, persistent button menu.
* **Global Hotkey**: A keyboard shortcut can be configured to trigger the `!skip` command from anywhere on the host machine.
* **Auto-Pause**: Automatically refreshes (pauses) the stream when the last user with a camera on leaves the VC.
* **State Persistence**: All critical data (stats, violations, timeouts) is saved to `data.json` and reloaded on startup.
* **Window Geometry Saving**: Remembers the browser's size and position between sessions.

### **🎵 Music Bot**

* **Dual Source Playback**: Play music from a local folder or stream directly from YouTube and other supported sites.
* **Full Queue Control**: Add songs, view the queue, skip, shuffle, loop, and clear the playlist.
* **Interactive Controls**: Manage the music queue and playback with intuitive commands.
* **Volume Adjustment**: Fine-tune the bot's volume for the perfect listening experience.
* **Music Hotkeys**: Set global hotkeys for common music commands like skip, pause, and volume control.

### **🛡️ Advanced Moderation & Automation**

* **Camera Enforcement**: Automatically mutes/deafens users without cameras in moderated VCs and applies escalating punishments (move to jail, short timeout, long timeout) for repeat violations.
* **Media-Only Channel**: Automatically deletes non-media messages in a designated channel.
* **VC Time Tracking**: Tracks the cumulative time users spend in moderated voice channels, with daily leaderboards.
* **Daily Auto-Stats**: Posts a full analytics report (VC time, command usage, etc.) daily at a configured time, then resets stats.
* **Comprehensive Logging**: Uses `loguru` for detailed, color-coded logs of all commands, moderation actions, and server events.
* **Rich Event Notifications**: Sends detailed embeds for member joins, leaves, kicks, bans, unbans, role changes, and timeouts.

## Command List

### 👤 User Commands

*(Requires being in the Streaming VC with camera on)*

* `!skip` / `!start`: Skips the current Omegle user.
* `!refresh` / `!pause`: Refreshes the Omegle page.
* `!info` / `!about`: Shows server information.
* `!rules`: Displays the server rules.
* `!times`: Shows the top 10 most active VC users.

### 🎶 Music Commands

* `!play <song name or URL>`: Plays a song or adds it to the queue.
* `!mskip`: Skips the current song.
* `!mpause`: Pauses or resumes the current song.
* `!mstop`: Stops the music and clears the queue.
* `!queue`: Displays the current music queue.
* `!vol <0-100>`: Sets the music volume.
* `!mvolup` / `!mvoldown`: Adjusts the volume up or down.
* `!mode <normal/loop/shuffle>`: Sets the playback mode.
* `!nowplaying`: Shows the currently playing song.

### 🛡️ Admin Commands

*(Requires Admin Role or being an Allowed User + Camera On)*

* `!help`: Sends the interactive help menu.
* `!bans` / `!banned`: Lists all banned users.
* `!timeouts`: Shows currently timed-out users.
* `!rtimeouts`: Removes all active timeouts.
* `!display <user>`: Shows a detailed profile for a user.
* `!commands`: Shows this list of all commands.

### 👑 Owner Commands (Allowed Users Only)

*(No channel or VC restrictions)*

* `!purge <count>`: Deletes a specified number of messages.
* `!shutdown`: Safely shuts down the bot.
* `!hush` / `!secret`: Server-mutes (and deafens) all non-admin users.
* `!rhush` / `!rsecret`: Removes server-mutes (and deafens).
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

## Setup & Configuration

### 1) Prerequisites

* **Microsoft Edge**: Ensure the Edge browser is installed.
* **Python 3.9+**: Install from [python.org](https://www.python.org/downloads/). Make sure to check "Add Python to PATH".
* **FFmpeg**: Required for the music bot. Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add it to your system's PATH.
* **Dependencies**: Open Command Prompt as an Administrator and run the following command:

    ```
    pip install discord.py python-dotenv selenium webdriver-manager loguru keyboard yt-dlp mutagen
    ```

### 2) Create a Discord Bot

1.  Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2.  Navigate to the "Bot" tab and enable the following **Privileged Gateway Intents**:
    * **Message Content Intent**
    * **Server Members Intent**
3.  Copy your **Bot Token**.
4.  Go to "OAuth2" -> "URL Generator". Select the `bot` and `applications.commands` scopes.
5.  Under "Bot Permissions", select `Administrator`.
6.  Copy the generated URL to invite the bot to your server.

### 3) File Setup

1.  Place all the bot's Python files (`bot.py`, `helper.py`, `omegle.py`, `tools.py`, `config.py`) in a single folder.
2.  Create a file named `.env` in the same folder.
3.  Add the following line to the `.env` file, replacing `your_token_here` with your bot token:
    ```
    BOT_TOKEN=your_token_here
    ```

### 4) Configure `config.py`

Open `config.py` and fill in the placeholder values with your server's IDs and desired settings. Enable Developer Mode in Discord to get IDs by right-clicking servers, channels, and users.

```
# --- ⚙️ DISCORD SERVER CONFIGURATION ⚙️ ---
GUILD_ID = 0           # Your Discord Server ID
CHAT_CHANNEL_ID = 0    # Channel for join/leave/ban notifications
COMMAND_CHANNEL_ID = 0 # Channel for bot commands and help menu
STREAMING_VC_ID = 0    # Main streaming voice channel
PUNISHMENT_VC_ID = 0   # VC where users are moved for a first violation

# --- 👑 SERVER OWNER PERMISSIONS 👑 ---
ALLOWED_USERS = {123456789012345678} # A set of user IDs with full bot access
ADMIN_ROLE_NAME = ["Admin", "Moderator"] # Roles with admin-level command access

# --- 🌐 BROWSER AUTOMATION (SELENIUM) 🌐 ---
OMEGLE_VIDEO_URL = "[https://omegle.com/video](https://omegle.com/video)"
# Find by going to edge://version/ in Edge and copying the "Profile path"
EDGE_USER_DATA_DIR = "C:/Users/YourUser/AppData/Local/Microsoft/Edge/User Data"
# (Optional) Manually specify path to msedgedriver.exe if automatic download fails
EDGE_DRIVER_PATH = None

# --- 🎵 MUSIC BOT CONFIGURATION 🎵 ---
MUSIC_ENABLED = True
# (Optional) The FULL path to your local music directory.
MUSIC_LOCATION = "C:/Users/YourUser/Music"
MUSIC_BOT_VOLUME = 0.2  # Default volume (0.0 to 2.0)
MUSIC_MAX_VOLUME = 1.0  # Max volume for the !vol command

# ... and other settings ...
```

### 5) Running the Bot

1.  **Important**: Close all running instances of the Microsoft Edge browser.
2.  Open your command prompt, navigate to the bot's folder (`cd path/to/your/bot`).
3.  Run the bot using:
    ```
    python bot.py
    ```
4.  The bot will launch Edge, navigate to the configured URL, and start all systems.
5.  **Troubleshooting**:
    * **Token Error**: Ensure your `.env` file is correct and in the same folder.
    * **Edge Fails to Launch**: Double-check the `EDGE_USER_DATA_DIR` path in `config.py`.
    * **Music Bot Errors**: Ensure FFmpeg is installed and accessible in your system's PATH.
    * Check the `bot.log` file for detailed error messages.
