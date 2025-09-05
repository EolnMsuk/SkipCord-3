# SkipCord-3: A Discord Omegle / Music / Mod Bot

SkipCord-3 is a powerful, fully modular Discord bot designed for streamers who use Omegle or similar platforms. It seamlessly integrates a shared streaming experience into a Discord voice channel, empowering the audience with control through a clean, interactive button menu. The bot features advanced moderation, detailed logging, automated rule enforcement, and a complete music system, all built on a fully asynchronous architecture for rock-solid performance.


## 📚 Table of Contents

- [Key Features](#-key-features)

- [Command List](#-command-list)

- [How to Setup](#%EF%B8%8F-setup--configuration)

-----

## ✨ Key Features

### 🌐 Interactive Stream Control

  * **Intuitive Button Menus**: Users control the stream (`!skip`, `!info`) and music (`!mskip`, `!mpauseplay`) with persistent button menus that periodically refresh.
  * **Global Hotkeys**: Configure system-wide keyboard shortcuts to trigger commands like `!skip`, `!mskip`, `!mpauseplay`, and volume controls from anywhere on the host machine.
  * **Auto-Pause**: Intelligently refreshes (pauses) the stream when the last user with their camera on leaves the VC or turns their camera off, saving bandwidth.

<img width="378" height="201" alt="omegle" src="https://github.com/user-attachments/assets/91c01887-b196-46c3-892c-03b6eb383f38" />

### 🛡️ Advanced Moderation & Automation

  * **Camera Enforcement**: Automatically mutes/deafens users without cameras in moderated VCs and applies escalating punishments for repeat violations (VC move -\> short timeout -\> long timeout).
  * **Daily Auto-Stats**: Posts a full analytics report daily at a configured UTC time, then automatically clears all statistics for the next day.
  * **Media-Only Channels**: Enforces rules in designated channels by automatically deleting any messages that do not contain an image, video, link, or other media.
  * **Comprehensive Logging**: Utilizes `loguru` for detailed, color-coded logs of all commands, moderation actions, and server events, saved to `bot.log`.

<img width="1271" height="538" alt="console" src="https://github.com/user-attachments/assets/58125182-849d-4392-ae15-e561b5f4e8fa" />

### 🎵 Integrated Music System

  * **Versatile Playback**: Search / play songs or playlists from **YouTube** / **Spotify** / local files.
  * **Interactive Queue**: View the song queue with `!q` and instantly jump to any song using a dropdown menu.
  * **Persistent Playlists**: Save the current queue as a named playlist, then load, list, or delete playlists at any time.
  * **Multiple Playback Modes**: Effortlessly cycle between **Shuffle**, **Alphabetical**, and **Loop** modes.
  * **Automatic Management**: The bot joins the VC when users with cameras are present and leaves when it's empty to conserve resources.

<img width="420" height="282" alt="panel" src="https://github.com/user-attachments/assets/8a10d47b-067c-4034-8a9a-7ca8ef1c2baf" />

### 📊 Persistent State & Analytics

  * **State Persistence**: All critical data—stats, violations, timeouts, event history, and playlists—is saved to `data.json` and reloaded on startup, ensuring no data is lost after a crash or restart.
  * **VC Time Tracking**: Tracks the cumulative time users spend in moderated voice channels, with daily leaderboards available via the `!times` command.

<img width="376" height="322" alt="times" src="https://github.com/user-attachments/assets/4592939a-167f-4dcd-b6d4-c5d8b9ac0aa3" />

### 🔔 Comprehensive Event Notifications

The bot keeps administrators informed with a robust, event-driven notification system. It uses rich, detailed embeds to provide real-time updates for all significant server activities:

  * **Member Activity**: Joins, Leaves (batched for mass departures), Kicks, Bans, and Unbans.
  * **Moderation Actions**: Timeouts Added/Removed and Role Changes.
  * **Bot & Stream Status**: Bot Online, Stream Auto-Pause, and Browser Health notifications.

<img width="445" height="493" alt="notifiy" src="https://github.com/user-attachments/assets/6257f651-fde3-4cd7-8e91-f0c6d26ebdca" />

-----

## 📋 Command List

### 👤 User Commands

*(Requires being in the Streaming VC with camera on)*

  * `!skip` / `!start`: Skips the current Omegle user.
  * `!refresh` / `!pause`: Refreshes the Omegle page.
  * `!info` / `!about`: Shows server information and rules.
  * `!rules`: Displays the server rules.
  * `!times`: Shows the top 10 most active VC users.
  * `!m` / `!msearch <query>`: Searches for a song/URL to add to the queue.
  * `!q` / `!queue`: Displays the interactive song queue.
  * `!np` / `!nowplaying`: Shows the currently playing song.
  * `!mskip`: Skips the current song.
  * `!mpp` / `!mpauseplay`: Toggles music play/pause.
  * `!mclear`: Clears all songs from the search queue.
  * `!mshuffle`: Cycles music mode (Shuffle -\> Alphabetical -\> Loop).
  * `!vol` / `!volume <0-100>`: Sets the music volume.
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
  * `!hush`: Server-mutes all non-admin users.
  * `!rhush` / `!removehush`: Removes server-mutes.
  * `!secret`: Server-mutes and deafens all non-admin users.
  * `!rsecret` / `!removesecret`: Removes server-mutes and deafens.
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
  * `!shutdown`: Safely shuts down the bot.

-----

## ⚙️ Setup & Configuration

### 1\. Prerequisites

  * **Microsoft Edge**: Ensure the Edge browser is installed and up-to-date.
  * **Python 3.9+**: Install from [python.org](https://www.python.org/downloads/). Make sure to check **"Add Python to PATH"** during installation.
  * **FFmpeg**: Required for music playback. Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add it to your system's PATH.
  * **Dependencies**: Open `cmd.exe` or another terminal, then paste and run the following command:

    ```
    pip install discord.py python-dotenv selenium loguru keyboard mutagen yt-dlp spotipy pyautogui
    ```

### 2\. Create a Discord Bot

1.  Navigate to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2.  Go to the **"Bot"** tab and enable the following **Privileged Gateway Intents**:
      * ✅ **Message Content Intent**
      * ✅ **Server Members Intent**
3.  Click **"Reset Token"** to reveal your bot's token. **Copy this value immediately and store it securely.**
4.  Go to the **"OAuth2" -\> "URL Generator"** tab. Select the `bot` and `applications.commands` scopes.
5.  In the "Bot Permissions" section, select `Administrator`.
6.  Copy the generated URL and use it to invite the bot to your server.

### 3\. Set up Spotify API (Optional)

To enable playing songs, albums, and playlists from Spotify links, you need API credentials.

1.  Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/) and log in.
2.  Click **"Create app"**.
3.  Give your app a **Name** and **Description** (e.g., "SkipCord Bot") and agree to the terms.
4.  Once created, you will see your **Client ID**. Click **"Show client secret"** to reveal the **Client Secret**.
5.  **Copy both the Client ID and Client Secret.** You will need them for the next step.

### 4\. File Setup

1.  Create a folder for your bot and place all the provided Python files (`bot.py`, `helper.py`, `omegle.py`, `tools.py`) inside.

3.  Open the `.env` file and add your credentials in the following format. Replace the placeholder text with the actual values you copied.

    ```env
    # .env file
    BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
    SPOTIPY_CLIENT_ID=YOUR_SPOTIFY_CLIENT_ID_HERE
    SPOTIPY_CLIENT_SECRET=YOUR_SPOTIFY_CLIENT_SECRET_HERE
    ```

    > **Note:** If you are not setting up Spotify, you can leave the `SPOTIPY` lines blank, but the `BOT_TOKEN` is required.

### 5\. Configure `config.py`

Open `config.py` and replace the placeholder values with your server's specific IDs and settings. To get IDs, enable Developer Mode in Discord, then right-click a server, channel, or user and select "Copy ID".

```python
# --- REQUIRED SETTINGS ---
GUILD_ID = 123456789012345678                # Your Discord Server ID
COMMAND_CHANNEL_ID = 123456789012345678      # Channel for bot commands and menus
CHAT_CHANNEL_ID = 123456789012345678         # Channel for join/leave/ban notifications
STREAMING_VC_ID = 123456789012345678         # Main streaming/music voice channel
PUNISHMENT_VC_ID = 123456789012345678        # VC where users are moved for a first violation
OMEGLE_VIDEO_URL = "https://uhmegle.com/video"     # URL for the streaming website
# Find by going to edge://version/ in your browser and copying the "Profile path"
EDGE_USER_DATA_DIR = "C:/Users/YourUser/AppData/Local/Microsoft/Edge/User Data"

# --- PERMISSIONS ---
ALLOWED_USERS = {123456789012345678, 987654321098765432} # User IDs with full bot access
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

-----

## Running the Bot

1.  **Important**: Close all running instances of the Microsoft Edge browser.
2.  Open your command prompt, navigate to the bot's folder (`cd path/to/your/bot`), and run the bot:

    ```
    python bot.py
    ```
4.  The bot will launch Edge, navigate to your configured URL, and initialize all systems.

### Troubleshooting

  * **Token Error**: Ensure your `.env` file is correctly named (it must be `.env`, not `env.txt`), is in the same folder as `bot.py`, and contains the correct Discord bot token.
  * **Edge Won't Launch**: Double-check that the `EDGE_USER_DATA_DIR` path in `config.py` is absolutely correct and matches your system.
  * **"WebDriver" Error**: Make sure your Edge browser is fully updated. Selenium's automatic driver management requires an up-to-date browser.
  * **Music Doesn't Play**: Confirm that **FFmpeg** is installed and its location is included in your system's PATH environment variable.
  * **Spotify Links Fail**: Check your `.env` file to ensure the `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET` are correct and have no extra spaces.
  * **Other Issues**: Check the `bot.log` file in the bot's folder for detailed error messages.
