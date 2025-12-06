# SkipCord-3.9: A Discord / Omegle / Music Bot

This bot integrates a group Omegle screenshare to a Discord VC, allowing the VC users to actively Skip, Report, Pause etc through dynamically embedded buttons. It safeguards your community with intelligent auto-moderation and includes a complete music system, automated ban detection with evidence logging and detailed daily analytics.

  - [Key Features](#-key-features)
  - [Commands](#-command-list)
  - [How to Setup](#%EF%B8%8F-setup--configuration)
  - [Donate](#donate)

## ✨ Key Features

### 🌐 Interactive Stream Control

  * **Intuitive Button Menus**: Users control the stream (`!skip` ⏭️, `!refresh` 🔄, `!rules` ℹ️) and music (`!mskip`, `!mpauseplay`, `!mclear`) with persistent button menus. Requires being in the Streaming VC with camera on for most actions.
  * **Cloudflare & Security Bypass**: Includes advanced logic to detect and click "Verify you are human" (Cloudflare/Turnstile) checkboxes inside iframes, ensuring the stream recovers automatically after refreshes.
  * **Global Hotkeys**: Configure system-wide keyboard shortcuts to trigger commands like `!skip`, `!mskip`, `!mpauseplay`, and volume controls from anywhere on the host machine. *Version 3.7 adds connection safety checks to prevent crashes if hotkeys are pressed while disconnected.*
  * **Auto-Start**: Automatically starts the stream by running `!skip` as soon as the first user joins the streaming VC with their camera on (configurable).
  * **Smart Auto-Pause & Graceful Shutdown**: Triggers a refresh *only* when the last user leaves. Includes a **14-second grace period** prevents the bot from rapidly toggling security tasks if a user rejoins quickly.
  * **Public Action Feed**: Button commands like `!skip` are announced publicly in the command channel (with auto-delete) for better transparency.

<img width="355" height="250" alt="omegle" src="https://github.com/user-attachments/assets/0acbceb4-0e08-46ad-85c6-0c6e6beb5702" />

### 🔔 Notification System

* **Smart Leave Batching**: Consolidates mass departures into single summaries to prevent chat spam, automatically highlighting users with roles.
* **Rich Moderation Logs**: Real-time, color-coded embeds for bans, kicks, timeouts, and Punishment VC moves, fetching reason and moderator details automatically from the audit logs.
* **New Member Intel**: Join alerts include account creation dates to instantly spot potential alt accounts.
* **Easy Toggles**: Enable or disable event logging instantly with `!enablenotifications` / `!disablenotifications`.

<img width="400" height="512" alt="noti" src="https://github.com/user-attachments/assets/3cfd9df8-5b80-4781-af40-576fcfc41b29" />

### 🎵 Integrated Music System

  * **Versatile Playback**: Search/play songs from **YouTube** / **Spotify** / local files.
  * **Role-Based Access**: Optionally restrict music control to specific roles using the `MUSIC_ROLES` configuration.
  * **Spotify Limits**: To prevent queue flooding, Spotify playlist loading is capped at 100 tracks per request.
  * **Interactive Queue**: View the song queue with `!q` and instantly jump to any song using a dropdown menu.
  * **Persistent Playlists**: Save/Load/Delete named playlists.
  * **Watchdog**: Ensures music playback automatically resumes if it stalls while listeners are present.

<img width="420" height="297" alt="music" src="https://github.com/user-attachments/assets/ce872a83-4f0e-46f0-90e5-4ab7de2a7cb0" />

### 🛡️ Advanced Moderation & Automation

  * **Camera & Deafen Enforcement**: Automatically mutes/deafens users without cameras in moderated VCs. **New in v3.7:** Now also tracks and punishes users who remain **self-deafened** for longer than the allowed time (default 300s).
  * **Persistent Moderation Report**: The `🛡️ Moderation Status 🛡️` menu updates in real-time (and persists through restarts) to show active timeouts, command-disabled users, and a log of recent manual untimeouts—identifying **exactly which moderator** removed a timeout.
  * **Automatic Ban Handling**: Periodically captures browser screenshots. When a ban is detected, it saves the screenshots locally, **posts them to a Discord channel**, and logs details to a dedicated `ban.log`.
  * **Daily Auto-Stats**: Posts a full analytics report (`!stats`) daily at a configured UTC time, then automatically clears VC time/usage statistics.

<img width="349" height="406" alt="times" src="https://github.com/user-attachments/assets/e4b23692-5e5c-422e-b7c6-bf7282aa668b" /> 
 
<img width="344" height="160" alt="mod" src="https://github.com/user-attachments/assets/6a7fda25-6518-470a-8281-3525ac94663b" />

### 📊 Persistent State & Analytics

  * **State Persistence**: All critical data—stats, violations, timeouts, event history, playlists, window geometry, and menu message IDs—is saved to `data.json`.
  * **VC Time Tracking**: Tracks cumulative time users spend in moderated voice channels, with daily leaderboards (`!times`).

<img width="1102" height="1084" alt="console" src="https://github.com/user-attachments/assets/ce468df4-4a6f-46fe-8b87-beef2f5f165a" />

## 📋 Command List

### 👤 User Commands

*(Requires being in the Streaming VC with camera on)*

* `!skip` - Skips the current Omegle user.
* `!refresh` - Refreshes the Omegle page.
* `!info` - Shows server info/rules.
* `!rules` - Shows the server rules.
* `!mskip` - Skips the current song.
* `!mpp` - Toggles music play and pause.
* `!vol 1-100` - Sets music volume (0-100).
* `!m songname` - Searches for songs/URLs.
* `!mclear` - Clears all songs from the search queue.
* `!np` - Shows currently playing song.
* `!q` - Displays the interactive song queue.
* `!playlist <save|load|list|delete> [name]` - Manages playlists.

### 🛡️ Admin Commands

*(Requires Admin Role or being an Allowed User + Camera On)*

* `!report` - Reports the current user on Omegle.
* `!moff` - Disables all music features and disconnects the bot.
* `!mon` - Enables all music features and connects the bot.
* `!rtimeouts` - Removes all active timeouts from users.
* `!display <user>` - Shows a detailed profile for a user.
* `!role <@role>` - Lists all members in a specific role.
* `!move <@user>` - Moves a user from Streaming to Punishment VC.
* `!commands` - Shows this list of all commands.

### 👑 Owner Commands (Allowed Users Only)

*(No channel or VC restrictions)*

* `!mshuffle` - Cycles music mode (Shuffle -> Alphabetical -> Loop).
* `!purge <count>` - Purges a specified number of messages.
* `!help` - Sends the interactive help menu with buttons.
* `!music` - Sends the interactive music control menu.
* `!times` - Shows top VC users by time.
* `!timeouts` - Shows currently timed-out users.
* `!bans` - Shows currently banned users.
* `!hush` - Server-mutes all non-admin users in the Streaming VC.
* `!rhush` / `!removehush` - Removes server-mutes from all users.
* `!secret` - Server-mutes and deafens all non-admin users.
* `!rsecret` / `!removesecret` - Removes mute/deafen from all users.
* `!modoff` / `!modon` - Toggles automated VC moderation.
* `!disablenotifications` / `!enablenotifications` - Toggles event notifications.
* `!ban <user>` - Bans user(s) with a reason prompt.
* `!unban <user_id>` - Unbans a user by ID.
* `!unbanall` - Unbans every user from the server.
* `!disable <user>` - Disables a user from using commands.
* `!enable <user>` - Re-enables a disabled user.
* `!top` - Lists the top 10 oldest server/Discord accounts.
* `!roles` - Lists all server roles and their members.
* `!admin` / `!owner` - Lists configured bot owners and admins.
* `!whois` - Shows a 24-hour report of server activity.
* `!stats` - Shows a detailed analytics report.
* `!join` - DMs a join invite to all users with an admin role.
* `!clearstats` - Clears all statistical data.
* `!clearwhois` - Clears all historical event data.
* `!shutdown` - Safely shuts down the bot.

## ⚙️ Setup & Configuration

### 1\. Prerequisites

  * **Microsoft Edge**: Ensure the Edge browser is installed and up-to-date.
  * **Python 3.9+**: Install from [python.org](https://www.python.org/downloads/). Make sure to check **"Add Python to PATH"** during installation.
  * **FFmpeg**: Required for music playback. Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add it to your system's PATH.
  * **Deno**: Required by the `yt-dlp` music dependency.
      1.  Install Deno from [deno.land/install](https://deno.land/install).
      2.  Ensure the Deno executable is added to your system's `PATH`.
  * **Dependencies**: Open `cmd.exe` or another terminal, then paste and run the following command:

```bash
pip install discord.py python-dotenv selenium loguru keyboard mutagen yt-dlp spotipy
````

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

To enable playing songs from Spotify links:

1.  Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/) and log in.
2.  Click **"Create app"**.
3.  Once created, you will see your **Client ID**. Click **"Show client secret"** to reveal the **Client Secret**.
4.  **Copy both.**

### 4\. File Setup

1.  Create a folder for your bot and place all the provided Python files (`bot.py`, `helper.py`, `omegle.py`, `tools.py`) inside.
2.  Create a new file in the same folder named `.env`.
3.  Open the `.env` file and add your credentials.
4.  **Install Cookies**: To enable age restricted youtube music, use a browser extension (like "Get cookies.txt LOCALLY") to export your YouTube cookies. Rename the file to `cookies.txt` and place it in the same folder as `bot.py`.

<!-- end list -->

```
# .env file
BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
SPOTIPY_CLIENT_ID=YOUR_SPOTIFY_CLIENT_ID_HERE
SPOTIPY_CLIENT_SECRET=YOUR_SPOTIFY_CLIENT_SECRET_HERE
```

### 5\. Configure `config.py`

Open `config.py` and replace the placeholder values with your server's settings.

```python
# --- REQUIRED SETTINGS ---
GUILD_ID = 123456789012345678                    # Your Discord Server ID
COMMAND_CHANNEL_ID = 123456789012345678          # Channel for bot commands and menus
CHAT_CHANNEL_ID = 123456789012345678             # Channel for join/leave/ban notifications
STREAMING_VC_ID = 123456789012345678             # Main streaming/music voice channel
PUNISHMENT_VC_ID = 123456789012345678            # VC where users are moved for a first violation
OMEGLE_VIDEO_URL = "[https://umingle.com/video](https://umingle.com/video)"   # URL for the streaming website
# Go to edge://version/ in edge and copy "Profile path" without the "/Default" or "/Profile X" at the end.
EDGE_USER_DATA_DIR = "C:/Users/YourUser/AppData/Local/Microsoft/Edge/User Data/"
EDGE_DRIVER_PATH = None # Optional: "C:/path/to/msedgedriver.exe"
SS_LOCATION = 'screenshots' # Local folder for screenshots

# --- PERMISSIONS ---
ALLOWED_USERS = {123456789012345678, 987654321098765432} # User IDs with full bot access
ADMIN_ROLE_NAME = ["Admin", "Moderator"]                 # Roles that can use Admin Commands
MOVE_ROLE_NAME = ["Admin", "Mover"]                      # Roles allowed to use the !move command
MUSIC_ROLES = ["DJ", "Supporter"]                        # Roles allowed to use Music commands (Leave empty for all)

# --- OPTIONAL FEATURES ---
LOG_GC = None                           # Channel ID for bot status/error messages
ALT_VC_ID = []                          # List of additional voice channel IDs to moderate
AUTO_STATS_CHAN = 123456789012345678    # Channel for daily stats & BAN SCREENSHOTS
MEDIA_ONLY_CHANNEL_ID = None            # Channel where only media is allowed
MOD_MEDIA = False                       # Enable/disable media-only channel moderation
EMPTY_VC_PAUSE = True                   # Auto-refresh (!pause) stream when VC becomes empty
AUTO_VC_START = False                   # Auto-skip (!start) stream when first user joins
AUTO_RELAY = False                      # Automatically send /relay to chat
AUTO_OMEGLE_VOL = False                 # Automatically set the Omegle volume slider
OMEGLE_VOL = 100                        # Volume (0-100) to set if enabled

STATS_EXCLUDED_USERS = {123456789012345678} # User IDs to exclude from stats

# --- TIMING & MESSAGES ---
AUTO_STATS_HOUR_UTC = 5                 # UTC hour for daily stats
AUTO_STATS_MINUTE_UTC = 0               # UTC minute for daily stats
COMMAND_COOLDOWN = 5                    # Button cooldown
CAMERA_OFF_ALLOWED_TIME = 30            # Seconds allowed without camera
DEAFEN_ALLOWED_TIME = 300               # Seconds allowed self-deafened before punishment
TIMEOUT_DURATION_SECOND_VIOLATION = 60
TIMEOUT_DURATION_THIRD_VIOLATION = 300
```

## Running the Bot

1.  **Important**: Close all running instances of Microsoft Edge.
2.  Open your terminal, navigate to the folder, and run:
    ```
    python bot.py
    ```

### Troubleshooting

  * **Token Error**: Ensure `.env` is named correctly and contains no spaces around the token.
  * **Edge Won't Launch**: Close all background Edge processes. Verify `EDGE_USER_DATA_DIR` path uses forward slashes `/`.
  * **Music Fails**: Ensure **FFmpeg** and **Deno** are in your system PATH.
  * **Spotify Links**: Check Client ID/Secret in `.env`. Note that playlists are limited to 100 tracks.
  * **VC Errors**: Check the `LOG_GC` channel. Ensure the bot has "Connect" and "Speak" permissions.

### Donate

  * **CashApp:** `cash.app/$eolnmsuk`
  * **Bitcoin:** `bc1qm06lzkdfule3f7flf4u70xvjrp5n74lzxnnfks`
