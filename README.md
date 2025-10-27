# SkipCord-3: A Discord / Omegle / Music Bot

SkipCord-3 is a powerful, modular Discord bot designed for streamers or channels who want to use Omegle or similar platforms as a group. It seamlessly integrates a shared streaming experience into a Discord voice channel, empowering the audience with control through an autonomous, interactive button menu. The bot features advanced moderation, detailed logging, automated rule enforcement, and a complete music system, all built on a fully asynchronous architecture for rock-solid performance.

  - [Key Features](#-key-features)
  - [Commands](#-command-list)
  - [How to Setup](#%EF%B8%8F-setup--configuration)
  - [Donations](#donations)

## ✨ Key Features

### 🌐 Interactive Stream Control

* **Intuitive Button Menus**: Users control the stream (`!skip`, `!refresh`, `!report`, `!rules`) and music (`!mskip`, `!mpauseplay`, `!mshuffle`, `!mclear`) with persistent button menus. Requires being in the Streaming VC with camera on for most actions (except for Allowed Users). The music menu dynamically updates with the current song, playback status, mode, volume, and queue length. Includes an auto-updating leaderboard for top VC users (`!times`).
* **Global Hotkeys**: Configure system-wide keyboard shortcuts to trigger commands like `!skip`, `!mskip`, `!mpauseplay`, and volume controls from anywhere on the host machine.
* **Auto-Start**: Automatically starts the stream by running `!skip` as soon as the first user joins the streaming VC with their camera on (configurable).
* **Auto-Pause**: Intelligently triggers a browser page refresh (`!refresh` / pause command) *only* when the last user with their camera on leaves the VC or turns their camera off, saving bandwidth. The bot also automatically handles common elements like terms checkboxes after a refresh.
* **Public Action Feed**: Button commands like `!skip` are announced publicly in the command channel (with auto-delete) for better transparency.

<img width="313" height="206" alt="omegle" src="https://github.com/user-attachments/assets/68a06e26-51a0-4943-8815-e66fa24973af" />

### 🛡️ Advanced Moderation & Automation

* **Camera Enforcement**: Automatically mutes/deafens users without cameras in moderated VCs and applies escalating punishments for repeat violations (VC move -> short timeout -> long timeout).
* **`!move` Command**: Admins or designated roles can move users from the Streaming VC to the Punishment VC (e.g., for sleeping), with automatic notifications and cooldowns for non-owners.
* **Automatic Ban Handling**: Periodically captures browser screenshots. When a ban is detected, it saves the recent screenshots locally, **posts them to a Discord channel** (configurable, auto-deleted after 2 minutes), and logs details (including users present in the streaming VC) to a dedicated `ban.log` file.
* **Clean Command Channel**: Automatically deletes old command messages (after ~1 min) to keep the control channel tidy, while preserving the interactive menus.
* **Daily Auto-Stats**: Posts a voice channel time report (`!times`) daily at a configured UTC time, then automatically clears VC time, command usage, and violation statistics for the next day.
* **Media-Only Channels**: Enforces rules in designated channels by automatically deleting any messages that do not contain an image, video, link, or other media.
* **Comprehensive Logging**: Utilizes `loguru` for detailed, color-coded logs of all commands, moderation actions, and server events, saved to `bot.log`. Includes a separate, persistent `ban.log` for ban-specific events, featuring auto-rotation and compression. Status messages and critical errors (like **VC connection failures**) can be sent to a dedicated Discord log channel (configurable via `LOG_GC`).

<img width="1271" height="538" alt="2" src="https://github.com/user-attachments/assets/3c6de1af-c9a7-4474-a498-dbeaf4af19de" />

### 🎵 Integrated Music System

* **Versatile Playback**: Search / play songs or playlists from **YouTube** / **Spotify** / local files. Filters unavailable/deleted videos during search and playlist processing.
* **Interactive Queue**: View the song queue with `!q` and instantly jump to any song using a dropdown menu.
* **Persistent Playlists**: Save the current queue as a named playlist, then load, list, or delete playlists at any time.
* **Multiple Playback Modes**: Effortlessly cycle between **Shuffle**, **Alphabetical**, and **Loop** modes.
* **Automatic Management**: The bot joins the VC when users with cameras are present and leaves when it's empty to conserve resources. Includes a watchdog task to ensure playback resumes if it unexpectedly stops.
* **Dynamic Menu**: The interactive music control menu updates in real-time to show the current song, playback status, volume, mode, and queue length.

<img width="420" height="282" alt="3" src="https://github.com/user-attachments/assets/e54fe401-1d86-4d06-8772-b966b38b3b75" />

### 📊 Persistent State & Analytics

* **State Persistence**: All critical data—stats, violations, timeouts, event history, playlists, window geometry, moderation settings, and menu message IDs—is saved to `data.json` and reloaded on startup, ensuring no data is lost after a crash or restart.
* **VC Time Tracking**: Tracks the cumulative time users spend in moderated voice channels, with daily leaderboards available via the `!times` command (also shown in the command channel menu).

<img width="376" height="322" alt="4" src="https://github.com/user-attachments/assets/f214d515-b9a3-4995-b2a1-bfbaf204e3b0" />

### 🔔 Comprehensive Event Notifications

The bot keeps administrators informed with a robust, event-driven notification system. It uses rich, detailed embeds to provide real-time updates for all significant server activities:

* **Member Activity**: Joins, Leaves (batched for mass departures), Kicks, Bans, and Unbans.
* **Moderation Actions**: Timeouts Added/Removed, Role Changes, **VC Moves**.
* **Bot & Stream Status**: Bot Online, Stream Auto-Pause/Start, Browser Health (including **VC connection errors** sent to the configured `LOG_GC` channel), and Omegle Ban/Unban status notifications (including **pre-ban screenshots posted to Discord**).

<img width="445" height="493" alt="5" src="https://github.com/user-attachments/assets/ede4dc10-f7fd-47d8-b228-ce8bd46a90ec" />

## 📋 Command List

### 👤 User Commands

*(Requires being in the Streaming VC with camera on)*

* `!skip` / `!start`: Skips the current Omegle user.
* `!refresh` / `!pause`: Refreshes the Omegle browser page (like F5) and attempts to handle initial prompts like checkboxes.
* `!info` / `!about`: Shows pre-configured server information messages.
* `!rules`: Displays the server rules.
* `!times`: Shows the top 10 most active VC users.
* `!m` / `!msearch <query>`: Searches for a song/URL/playlist to add to the queue.
* `!q` / `!queue`: Displays the interactive song queue with pagination and jump-to functionality.
* `!np` / `!nowplaying`: Shows details about the currently playing song.
* `!mskip`: Skips the current song. Disables loop mode if active.
* `!mpp` / `!mpauseplay`: Toggles music play/pause. Starts playback if stopped.
* `!mclear`: Clears all user-added songs from the queue after confirmation.
* `!mshuffle`: Cycles music mode (Shuffle -> Alphabetical -> Loop).
* `!vol` / `!volume <0-100>`: Sets the music volume (relative to `MUSIC_MAX_VOLUME`).
* `!playlist <save|load|list|delete> [name]`: Manages persistent song playlists.

### 🛡️ Admin Commands

*(Requires Admin Role or being an Allowed User + Camera On)*

* `!report`: Reports the current Omegle user and saves a screenshot locally.
* `!help`: Sends the interactive Omegle control menu.
* `!music`: Sends the interactive music control menu.
* `!bans` / `!banned`: Lists all banned users with reasons.
* `!timeouts`: Shows currently timed-out users and command-disabled users.
* `!rtimeouts`: Removes all active timeouts after confirmation.
* `!display <user>`: Shows a detailed profile embed for a user.
* `!role <role>`: Lists all members in a specified role.
* `!move <user>`: Moves a user from the Streaming VC to the Punishment VC (reason: Sleeping). Has a 1-hour cooldown for non-owners.
* `!commands`: Shows this list of all commands.
* `!mon`: Enables music features, connects the bot, and refreshes menus.
* `!moff`: Disables music features, clears queue, stops playback, and disconnects the bot.

### 👑 Owner Commands (Allowed Users Only)

*(No channel or VC restrictions)*

* `!purge <count>`: Deletes a specified number of messages.
* `!hush`: Server-mutes all non-owner/non-admin users in the Streaming VC.
* `!rhush` / `!removehush`: Removes server-mutes applied by `!hush`.
* `!secret`: Server-mutes and deafens all non-owner/non-admin users in the Streaming VC.
* `!rsecret` / `!removesecret`: Removes server-mutes/deafens applied by `!secret`.
* `!modon` / `!modoff`: Toggles automated VC moderation (camera checks, punishments).
* `!disablenotifications` / `!enablenotifications`: Toggles event notifications (leave, unban, kick, etc.).
* `!disable <user>`: Prevents a user from using any bot commands or buttons.
* `!enable <user>`: Re-enables a command-disabled user.
* `!ban <user_mention_or_id...>`: Bans one or more users with interactive reason prompt.
* `!unban <user_id,...>`: Unbans one or more users by ID after confirmation.
* `!unbanall`: Unbans every user from the server after confirmation.
* `!top`: Lists the top 10 oldest server members and Discord accounts.
* `!roles`: Lists all server roles and their members.
* `!admin` / `!owner`: Lists configured bot owners and admins.
* `!whois`: Shows a 24-hour report of all server activity (joins, leaves, bans, kicks, timeouts, etc.).
* `!stats`: Shows a detailed analytics report (VC time, command usage, violations).
* `!join`: DMs a pre-configured join invite message to all users with an admin role.
* `!clearstats`: Clears all statistical data after confirmation.
* `!clearwhois`: Clears all historical event data (`!whois`) after confirmation.
* `!shutdown`: Safely shuts down the bot, saving state.
* `!enableomegle`: Enables Omegle features and launches/initializes the browser.
* `!disableomegle`: Disables Omegle features and closes the browser.

## ⚙️ Setup & Configuration

### 1. Prerequisites

* **Microsoft Edge**: Ensure the Edge browser is installed and up-to-date.
* **Python 3.9+**: Install from [python.org](https://www.python.org/downloads/). Make sure to check **"Add Python to PATH"** during installation.
* **FFmpeg**: Required for music playback. Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add it to your system's PATH.
* **Dependencies**: Open `cmd.exe` or another terminal, then paste and run the following command:

```
pip install discord.py python-dotenv selenium loguru keyboard mutagen yt-dlp spotipy
```

### 2. Create a Discord Bot

1.  Navigate to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2.  Go to the **"Bot"** tab and enable the following **Privileged Gateway Intents**:
    * ✅ **Message Content Intent**
    * ✅ **Server Members Intent**
3.  Click **"Reset Token"** to reveal your bot's token. **Copy this value immediately and store it securely.**
4.  Go to the **"OAuth2" -> "URL Generator"** tab. Select the `bot` and `applications.commands` scopes.
5.  In the "Bot Permissions" section, select `Administrator`.
6.  Copy the generated URL and use it to invite the bot to your server.

### 3. Set up Spotify API (Optional)

To enable playing songs, albums, and playlists from Spotify links, you need API credentials.

1.  Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/) and log in.
2.  Click **"Create app"**.
3.  Give your app a **Name** and **Description** (e.g., "SkipCord Bot") and agree to the terms.
4.  Once created, you will see your **Client ID**. Click **"Show client secret"** to reveal the **Client Secret**.
5.  **Copy both the Client ID and Client Secret.** You will need them for the next step.

### 4. File Setup

1.  Create a folder for your bot and place all the provided Python files (`bot.py`, `helper.py`, `omegle.py`, `tools.py`) inside.
2.  Create a new file in the same folder named `.env` (note the leading dot).
3.  Open the `.env` file and add your credentials in the following format. Replace the placeholder text with the actual values you copied.

```
# .env file

BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
SPOTIPY_CLIENT_ID=YOUR_SPOTIFY_CLIENT_ID_HERE
SPOTIPY_CLIENT_SECRET=YOUR_SPOTIFY_CLIENT_SECRET_HERE
````

> **Note:** If you are not setting up Spotify, you can leave the `SPOTIPY` lines blank, but the `BOT_TOKEN` is required.

### 5. Configure `config.py`

Open `config.py` and replace the placeholder values with your server's specific IDs and settings. To get IDs, enable Developer Mode in Discord, then right-click a server, channel, or user and select "Copy ID".

```python
# --- REQUIRED SETTINGS ---
GUILD_ID = 123456789012345678                # Your Discord Server ID
COMMAND_CHANNEL_ID = 123456789012345678      # Channel for bot commands and menus
CHAT_CHANNEL_ID = 123456789012345678         # Channel for join/leave/ban notifications
STREAMING_VC_ID = 123456789012345678         # Main streaming/music voice channel
PUNISHMENT_VC_ID = 123456789012345678        # VC where users are moved for a first violation (or !move)
OMEGLE_VIDEO_URL = "[https://example-stream-site.com/video](https://example-stream-site.com/video)" # URL for the streaming website
# Go to edge://version/ in edge and copy the "Profile path" without the "/Default" or "/Profile X" at the end.
EDGE_USER_DATA_DIR = "C:/Users/YourUser/AppData/Local/Microsoft/Edge/User Data/"
# (Optional) Manually specify path to msedgedriver.exe if auto-detection fails
EDGE_DRIVER_PATH = None # Example: "C:/path/to/msedgedriver.exe"
# (Optional) Specify where ban screenshots are saved locally
SS_LOCATION = 'screenshots'

# --- PERMISSIONS ---
ALLOWED_USERS = {123456789012345678, 987654321098765432} # User IDs with full bot access (Owner Commands)
ADMIN_ROLE_NAME = ["Admin", "Moderator"] # Roles that can use Admin Commands
MOVE_ROLE_NAME = ["MoveRole"]           # Roles allowed to use the !move command (if not Allowed User)

# --- OPTIONAL FEATURES ---
LOG_GC = None                        # Channel ID for bot status/error messages (e.g., online, VC connect fail)
# (Set to None to disable Discord logging channel)
ALT_VC_ID = []                       # List of additional voice channel IDs to moderate (apply camera rules)
AUTO_STATS_CHAN = 123456789012345678 # Channel for daily auto-stats reports & BAN SCREENSHOTS
MEDIA_ONLY_CHANNEL_ID = None         # Channel where only media is allowed (automatically delete non-media messages)
MOD_MEDIA = True                     # Enable/disable media-only channel moderation
EMPTY_VC_PAUSE = True                # Auto-refresh (!pause) stream when VC becomes empty of camera users
AUTO_VC_START = False                # Auto-skip (!start) stream when first camera user joins an empty VC
STATS_EXCLUDED_USERS = {123456789012345678} # User IDs to exclude from !times, !stats

# --- TIMING & MESSAGES ---
AUTO_STATS_HOUR_UTC = 5              # UTC hour for daily auto-stats post & clear (0-23)
AUTO_STATS_MINUTE_UTC = 0            # UTC minute for daily auto-stats post & clear (0-59)
COMMAND_COOLDOWN = 5                 # Seconds cooldown for regular/button commands
CAMERA_OFF_ALLOWED_TIME = 30         # Seconds a user can have camera off before punishment
TIMEOUT_DURATION_SECOND_VIOLATION = 60  # Seconds for 2nd camera violation timeout
TIMEOUT_DURATION_THIRD_VIOLATION = 300 # Seconds for 3rd+ camera violation timeout
INFO_MESSAGES = ["Welcome! Rules: Camera on in VC.", "Use the buttons or commands below!"]
JOIN_INVITE_MESSAGE = "An admin has requested your presence in the stream! Join here: <#CHANNEL_ID>" # Replace CHANNEL_ID
RULES_MESSAGE = """## Welcome to the Server!
**Rule 1:** Be respectful to others.
**Rule 2:** Keep your camera on in the streaming voice channel.
**Rule 3:** No hateful or inappropriate content.
"""

# --- MUSIC BOT SETTINGS ---
MUSIC_ENABLED = True                 # Master toggle for all music features
# Path to a folder with local music files (e.g., "C:/Users/YourUser/Music")
MUSIC_LOCATION = None
MUSIC_BOT_VOLUME = 0.2               # Default volume (0.0 to 1.0) applied on startup/play
MUSIC_MAX_VOLUME = 1.0               # Maximum volume allowed for the !vol command (scaled: 100% = this value)
MUSIC_DEFAULT_ANNOUNCE_SONGS = False # Announce every song change in chat
NORMALIZE_LOCAL_MUSIC = True         # Apply audio normalization to local files (requires FFmpeg, can increase CPU usage)
MUSIC_SUPPORTED_FORMATS = ('.mp3', '.flac', '.wav', '.ogg', '.m4a') # File types to scan

# --- GLOBAL HOTKEYS (Run on the machine hosting the bot) ---
ENABLE_GLOBAL_HOTKEY = True
GLOBAL_HOTKEY_COMBINATION = 'alt+grave'  # Hotkey for !skip (uses keyboard library names)
ENABLE_GLOBAL_MSKIP = True
GLOBAL_HOTKEY_MSKIP = '`'              # Hotkey for !mskip
ENABLE_GLOBAL_MPAUSE = True
GLOBAL_HOTKEY_MPAUSE = 'page down'     # Hotkey for !mpauseplay
ENABLE_GLOBAL_MVOLUP = True
GLOBAL_HOTKEY_MVOLUP = ']'             # Hotkey for volume up (+5%)
ENABLE_GLOBAL_MVOLDOWN = True
GLOBAL_HOTKEY_MVOLDOWN = '['           # Hotkey for volume down (-5%)
````

## Running the Bot

1.  **Important**: Close all running instances of the Microsoft Edge browser. This ensures the bot can take control of the user data directory properly.

2.  Open your command prompt or terminal.

3.  Navigate to the folder where you saved the bot files using the `cd` command (e.g., `cd C:\Users\YourUser\Desktop\SkipCord`).

4.  Run the bot using Python:

    ```
    python bot.py
    ```

5.  The bot should now start, log its initialization steps in the console, automatically launch Edge, navigate to your configured URL, and set up the interactive menus in Discord.

### Troubleshooting

  * **Token Error / Login Failure**: Ensure your `.env` file is correctly named (it must be `.env`, not `env.txt`), is in the same folder as `bot.py`, and contains the correct Discord bot token copied from the Developer Portal. Make sure there are no extra spaces.
  * **Edge Won't Launch / `user data directory is already in use`**: Double-check that **all** Edge browser windows and background processes are completely closed before starting the bot. Verify the `EDGE_USER_DATA_DIR` path in `config.py` is absolutely correct (use forward slashes `/` even on Windows) and points to the *parent* directory of `Default` or `Profile X`.
  * **"WebDriver" Error / Version Mismatch**: Make sure your Edge browser is fully updated (`edge://settings/help`). Selenium usually downloads the correct driver automatically. If you get persistent errors mentioning version mismatches, you can manually download the correct `msedgedriver.exe` for your specific Edge version from the [Microsoft Edge WebDriver page](https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/) and specify its full path (including `msedgedriver.exe`) in `config.py` via the `EDGE_DRIVER_PATH` setting.
  * **Music Doesn't Play / "FFmpeg not found"**: Confirm that **FFmpeg** is installed correctly and that the folder containing `ffmpeg.exe` is added to your system's `PATH` environment variable. You might need to restart your terminal or PC after updating the PATH. Check `bot.log` for specific FFmpeg errors during playback attempts.
  * **Spotify Links Fail**: Check your `.env` file to ensure the `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET` are correct, copied directly from the Spotify Developer Dashboard, and have no extra spaces.
  * **VC Connection Errors in `LOG_GC`**: If you see messages about failing to connect, check the bot's permissions in Discord. Ensure it has the "Connect" and "Speak" permissions for the `STREAMING_VC_ID`.
  * **Buttons Don't Work / Commands Fail**: Check the console output and `bot.log` for any error messages immediately after trying to use a command or button. Ensure you meet the requirements (e.g., in VC with camera on). Check if the user might be command-disabled (`!timeouts`).
  * **Other Issues**: Check the `bot.log` and `ban.log` files in the bot's folder for detailed error messages. Check the configured `LOG_GC` channel in Discord (if set) for status messages and critical errors.

### Donations

  * **CashApp:** `$eolnmsuk`
  * **Bitcoin:** `bc1qm06lzkdfule3f7flf4u70xvjrp5n74lzxnnfks`
