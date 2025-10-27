# omegle.py
# This file contains the OmegleHandler class, which is responsible for all browser
# automation tasks using Selenium. It manages the WebDriver lifecycle and provides
# methods to interact with the Omegle web page.

import asyncio
import os
import re
import base64
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Optional, Union, List, Tuple

import discord
from discord.ext import commands
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, StaleElementReferenceException, UnexpectedAlertPresentException, NoSuchElementException
from selenium.webdriver.edge.service import Service
# --- [NEW IMPORTS] ---
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
# --- [END NEW IMPORTS] ---
from loguru import logger

# Local application imports
import config  # Used for SKIP_COMMAND_KEY if defined
from tools import BotConfig, BotState

# Constants
DRIVER_INIT_RETRIES = 2     # Number of times to retry initializing the WebDriver if it fails.
DRIVER_INIT_DELAY = 5       # Seconds to wait between retry attempts.
SCREENSHOT_JPEG_QUALITY = 75 # Quality for JPEG screenshots (0-100). 75 is a good balance.

def require_healthy_driver(func):
    """
    A decorator that checks if the Selenium WebDriver is initialized and responsive
    before allowing a method to execute. If the driver is not healthy, it will
    attempt to relaunch it. If the relaunch fails, it notifies the user.
    """
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        # A helper to find a context object (ctx) from the function's arguments to send a reply.
        def find_context() -> Optional[Union[commands.Context, discord.Message, discord.Interaction]]:
            ctx = None
            if args:
                ctx_candidate = args[0]
                if isinstance(ctx_candidate, (commands.Context, discord.Message, discord.Interaction)):
                    return ctx_candidate
            if 'ctx' in kwargs:
                ctx_candidate = kwargs['ctx']
                if isinstance(ctx_candidate, (commands.Context, discord.Message, discord.Interaction)):
                    return ctx_candidate
            return None

        # A helper to send a message to a context object, handling different context types.
        async def send_to_context(ctx, msg, ephemeral=False):
            if not ctx: return
            try:
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done(): await ctx.followup.send(msg, ephemeral=ephemeral)
                    else: await ctx.response.send_message(msg, ephemeral=ephemeral)
                elif hasattr(ctx, 'send'):
                    await ctx.send(msg)
            except Exception as e:
                logger.error(f"Failed to send message to context: {e}")

        # Check the health of the driver.
        if not await self.is_healthy():
            # Use a lock to prevent multiple coroutines from trying to relaunch at the same time.
            async with self._init_lock:
                # Re-check health after acquiring the lock, in case another coroutine already fixed it.
                if not await self.is_healthy():
                    logger.warning("Driver is unhealthy. Attempting to relaunch the browser...")
                    ctx = find_context()

                    await send_to_context(ctx, "Browser connection lost. Attempting to relaunch...")

                    # Attempt to re-initialize the driver.
                    if not await self.initialize():
                        logger.critical("Failed to relaunch the browser after retries. Manual restart required.")
                        await send_to_context(ctx, "Failed to relaunch the browser. Please restart the bot manually.", ephemeral=True)
                        return False # Indicate that the relaunch failed.

                    logger.info("Browser relaunched successfully.")
                    await send_to_context(ctx, "Browser has been successfully relaunched.")

        # If the driver was healthy initially OR was successfully relaunched, execute the original function.
        return await func(self, *args, **kwargs)
    return wrapper


class OmegleHandler:
    """
    Manages all Selenium WebDriver interactions for controlling the Omegle stream.
    This includes initializing the browser, navigating, and sending commands.
    """

    async def _set_volume(self, volume_percentage: int = 50) -> bool:
        """
        Internal helper to set the volume slider on the page.
        Returns True on success, False on failure.
        """
        logger.info(f"Attempting to set volume to {volume_percentage}%...")
        try:
            set_volume_script = f"""
            var slider = document.getElementById('vol-control');
            if (slider) {{
                slider.value = {volume_percentage};
                var event = new Event('input', {{ bubbles: true }});
                slider.dispatchEvent(event);
                console.log('Volume set to {volume_percentage}%');
                return true;
            }} else {{
                console.error('Volume slider #vol-control not found.');
                return false;
            }}
            """
            volume_set = await asyncio.to_thread(self.driver.execute_script, set_volume_script)
            if volume_set:
                logger.info(f"Successfully executed script to set volume to {volume_percentage}%.")
                return True
            else:
                logger.warning("Volume slider element not found via script.")
                return False
        except Exception as e:
            logger.error(f"Error during volume automation: {e}")
            return False

    async def _attempt_send_relay(self) -> bool:
        """
        Internal helper to send the /relay command and set the volume.
        It checks if the command has already been sent and updates the state on success.
        Returns True on success, False on failure.
        """
        async with self.state.moderation_lock:
            if not self.state or self.state.relay_command_sent:
                return True  # Already sent or state unavailable, so it's "successful".

            logger.info("Attempting to send /relay command and set volume...")

            # Set volume before sending the relay message
            await self._set_volume()

            try:
                chat_input_selector = "textarea.messageInput"
                send_button_xpath = "//div[contains(@class, 'mainText') and text()='Send']"

                def send_relay_command():
                    try:
                        # Give the page a moment to ensure elements are interactable
                        time.sleep(1.0)
                        chat_input = self.driver.find_element("css selector", chat_input_selector)
                        chat_input.send_keys("/relay")
                        time.sleep(0.5) # Wait briefly after typing
                        send_button = self.driver.find_element("xpath", send_button_xpath)
                        send_button.click()
                        return True
                    except Exception as e:
                        logger.warning(f"Could not find/interact with chat elements to send /relay. Will retry on next skip. Error: {e}")
                        return False

                relay_sent = await asyncio.to_thread(send_relay_command)

                if relay_sent:
                    self.state.relay_command_sent = True # Set the flag *only* on success inside the lock
                    logger.info("Successfully sent /relay command and updated state.")
                    return True
            except Exception as e:
                logger.error(f"An unexpected error occurred when trying to send /relay: {e}")

            logger.warning("Failed to send /relay command. Will retry on next skip.")
            return False


    def __init__(self, bot: commands.Bot, bot_config: BotConfig):
        self.bot = bot
        self.config = bot_config
        self.driver: Optional[webdriver.Edge] = None
        self._driver_initialized = False # A flag to track if the driver has been successfully initialized.
        self.state: Optional[BotState] = None
        self._init_lock = asyncio.Lock() # Lock to prevent race conditions during driver initialization.

    async def initialize(self) -> bool:
        """
        Initializes the Selenium Edge WebDriver with enhanced anti-detection measures.
        It first tries Selenium's automatic manager, then falls back to a specified
        driver path if the first attempt fails. It also sets initial volume.

        Returns:
            True if the driver was initialized successfully, False otherwise.
        """
        for attempt in range(DRIVER_INIT_RETRIES):
            try:
                if self.driver is not None:
                    await self.close()

                options = webdriver.EdgeOptions()

                # --- Stealth and stability arguments ---
                options.add_argument(f"user-data-dir={self.config.EDGE_USER_DATA_DIR}")
                options.add_argument('--ignore-certificate-errors')
                options.add_argument('--allow-running-insecure-content')
                options.add_argument("--log-level=3")
                options.add_argument('--disable-blink-features=AutomationControlled')
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-infobars")
                options.add_argument("--disable-popup-blocking")
                options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
                options.add_experimental_option('useAutomationExtension', False)

                # --- [NEW] Primary Initialization (Automatic) ---
                try:
                    logger.info("Initializing Selenium with automatic driver management...")
                    self.driver = await asyncio.to_thread(webdriver.Edge, options=options)
                    logger.info("Automatic driver management successful.")
                except WebDriverException as auto_e:
                    logger.warning(f"Automatic driver management failed: {auto_e}")
                    # --- [NEW] Fallback Initialization (Manual Path) ---
                    if self.config.EDGE_DRIVER_PATH and os.path.exists(self.config.EDGE_DRIVER_PATH):
                        logger.info(f"Attempting fallback with specified driver path: {self.config.EDGE_DRIVER_PATH}")
                        try:
                            service = Service(executable_path=self.config.EDGE_DRIVER_PATH)
                            self.driver = await asyncio.to_thread(webdriver.Edge, service=service, options=options)
                            logger.info("Fallback driver path successful.")
                        except Exception as fallback_e:
                            logger.error(f"Fallback driver path also failed: {fallback_e}")
                            raise fallback_e # Re-raise to be caught by the outer exception handler
                    else:
                        logger.warning("No fallback driver path specified or path is invalid. Retrying with automatic management.")
                        raise auto_e # Re-raise the original exception

                # --- Execute advanced stealth script before the website can run its own scripts ---
                stealth_script = """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.navigator.chrome = {
                    runtime: {},
                };
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3],
                });
                """
                await asyncio.to_thread(self.driver.execute_cdp_cmd, "Page.addScriptToEvaluateOnNewDocument", {"source": stealth_script})

                # Restore previous window geometry if it exists
                if self.state and self.state.window_size and self.state.window_position:
                    try:
                        logger.info(f"Restoring window to size: {self.state.window_size} and position: {self.state.window_position}")
                        def set_geometry():
                            self.driver.set_window_size(self.state.window_size['width'], self.state.window_size['height'])
                            self.driver.set_window_position(self.state.window_position['x'], self.state.window_position['y'])
                        await asyncio.to_thread(set_geometry)
                    except Exception as geo_e:
                        logger.error(f"Failed to restore window geometry: {geo_e}")

                # Navigate to the target URL
                logger.info(f"Navigating to {self.config.OMEGLE_VIDEO_URL}...")
                await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)
                # Add a small wait for the page elements to load after navigation
                await asyncio.sleep(1.0) # Wait 3 seconds

                # --- [NEW] Add pre-relay skip attempt during initialization ---
                logger.info("Attempting pre-relay skip during initialization...")
                try:
                    keys = getattr(config, "SKIP_COMMAND_KEY", None)
                    if not keys: keys = ["Escape", "Escape"]
                    if not isinstance(keys, list): keys = [keys]
                    
                    for i, key in enumerate(keys):
                        script = f"""
                        var evt = new KeyboardEvent('keydown', {{
                            bubbles: true, cancelable: true, key: '{key}', code: '{key}'
                        }});
                        document.dispatchEvent(evt);
                        """
                        await asyncio.to_thread(self.driver.execute_script, script)
                        logger.info(f"Selenium (init): Sent {key} key event to page.")
                        if i < len(keys) - 1:
                            await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"Pre-relay skip during initialization failed: {e}")
                # --- End of new block ---

                # --- Set Volume ---
                # await self._set_volume() # <-- REDUNDANT CALL REMOVED

                # Reset relay flag and attempt to send on startup
                if self.state:
                    self.state.relay_command_sent = False
                    logger.info("Relay flag reset. Attempting to send /relay on startup...")
                    await asyncio.sleep(1.0) # Give page a final moment
                    await self._attempt_send_relay()
                else:
                    logger.warning("Bot state not attached to omegle_handler, cannot send /relay on startup.")

                self._driver_initialized = True
                logger.info("Selenium driver initialized successfully.")
                return True

            except Exception as e:
                logger.error(f"Selenium initialization attempt {attempt + 1} failed: {e}")
                if "This version of Microsoft Edge Driver only supports" in str(e):
                    logger.critical("CRITICAL: WebDriver version mismatch. Please update Edge browser or check for driver issues.")
                if attempt < DRIVER_INIT_RETRIES - 1:
                    await asyncio.sleep(DRIVER_INIT_DELAY)

        logger.critical("Failed to initialize Selenium driver after retries.")
        self._driver_initialized = False
        return False

    async def is_healthy(self) -> bool:
        """
        Checks if the Selenium driver is initialized and still responsive.
        It does this by attempting to access a simple property (`current_url`).
        """
        if not self._driver_initialized or self.driver is None:
            return False
        try:
            # Run the blocking property access in a separate thread.
            await asyncio.to_thread(lambda: self.driver.current_url)
            return True
        except Exception:
            # Any exception here (e.g., browser crashed) means the driver is not healthy.
            return False

    async def get_window_geometry(self) -> Optional[tuple[dict, dict]]:
        """Gets the browser window's current size and position."""
        if not await self.is_healthy():
            return None
        try:
            # Run these blocking calls in a thread
            def get_geo():
                size = self.driver.get_window_size()
                position = self.driver.get_window_position()
                return size, position

            size, position = await asyncio.to_thread(get_geo)
            return size, position
        except Exception as e:
            logger.error(f"Could not get window geometry: {e}")
            return None

    async def close(self) -> None:
        """Safely closes the Selenium driver and quits the browser."""
        if self.driver is not None:
            try:
                # `driver.quit()` is a blocking call.
                await asyncio.to_thread(self.driver.quit)
                logger.info("Selenium driver closed.")
            except Exception as e:
                logger.error(f"Error closing Selenium driver: {e}")
            finally:
                self.driver = None
                self._driver_initialized = False

    # --- [NEW METHOD] ---
    @require_healthy_driver
    async def find_and_click_checkbox(self) -> bool:
        """
        Finds an <input type="checkbox"> element and clicks it using human-like mouse actions.
        """
        try:
            def perform_checkbox_click():
                # Find the checkbox element
                checkbox = self.driver.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                
                # Check if it's already selected
                if checkbox.is_selected():
                    logger.info("Checkbox already selected, no action needed.")
                    return False # Indicate no action was taken
                
                # Use ActionChains to emulate a human click
                logger.info("Checkbox found. Moving mouse to element and clicking...")
                actions = ActionChains(self.driver)
                actions.move_to_element(checkbox)
                actions.pause(0.5) # Brief pause over the element
                actions.click(checkbox)
                actions.perform()
                return True # Indicate action was taken

            clicked = await asyncio.to_thread(perform_checkbox_click)
            
            if clicked:
                logger.info("Successfully clicked the checkbox.")
            return True

        except NoSuchElementException:
            # This is not an error, it just means the checkbox isn't present.
            logger.info("No checkbox found on the page.")
            return False
        except Exception as e:
            logger.error(f"An error occurred while trying to click the checkbox: {e}", exc_info=True)
            return False
    # --- [END NEW METHOD] ---

    @require_healthy_driver
    async def custom_skip(self, ctx: Optional[commands.Context] = None) -> bool:
        """
        Executes a "skip" action. It first sends the /relay command if needed (e.g., after a refresh),
        ensures the browser is on the correct URL, then simulates key presses to skip the current chat.
        """
        # --- URL Verification & Redirect (Runs for all calls) ---
        try:
            current_url = await asyncio.to_thread(lambda: self.driver.current_url)

            if self.config.OMEGLE_VIDEO_URL not in current_url:
                logger.warning(f"URL Mismatch: Not on video page (Currently at: {current_url}). Redirecting before skip.")
                if ctx:
                    await ctx.send("Browser is on the wrong page. Redirecting to the stream now...", delete_after=10)
                await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)
                await asyncio.sleep(2.0)

        except Exception as e:
            logger.error(f"Failed during pre-skip URL check/redirect: {e}", exc_info=True)
            if ctx:
                await ctx.send("Error checking browser URL. The browser may be unresponsive.")
            return False # Return False here, as we can't do anything else.

        # --- [USER REQUESTED ORDER] 1. Standard Skip Logic ---
        keys = getattr(config, "SKIP_COMMAND_KEY", None)
        if not keys:
            keys = ["Escape", "Escape"]
        if not isinstance(keys, list):
            keys = [keys]

        skip_successful = False
        for attempt in range(3):
            try:
                for i, key in enumerate(keys):
                    script = f"""
                    var evt = new KeyboardEvent('keydown', {{
                        bubbles: true, cancelable: true, key: '{key}', code: '{key}'
                    }});
                    document.dispatchEvent(evt);
                    """
                    await asyncio.to_thread(self.driver.execute_script, script)
                    logger.info(f"Selenium: Sent {key} key event to page.")
                    if i < len(keys) - 1:
                        await asyncio.sleep(1) # Wait between keys if multiple are defined
                
                skip_successful = True # Mark as successful if the loop completes
                break # Exit retry loop on success

            except StaleElementReferenceException:
                logger.warning(f"StaleElementReferenceException on skip attempt {attempt + 1}. Retrying...")
                await asyncio.sleep(0.5)
                continue
            except Exception as e:
                logger.error(f"Selenium custom skip failed: {e}")
                if ctx: await ctx.send("Failed to execute skip command in browser.")
                # Don't return yet, still try to send relay/volume
                skip_successful = False # Ensure it's marked as failed
                break # Exit retry loop

        if not skip_successful:
            logger.error("Failed to execute custom skip. Will still attempt volume/relay.")
            # Don't return, as per user's logic to run volume/relay anyway.

        # --- [USER REQUESTED ORDER] 2 & 3. Set Volume and Send /relay ---
        # We run this *after* the skip, as requested.
        # _attempt_send_relay() already contains _set_volume(), so we just call it.
        # This helper has a built-in check so it only runs once per "armed" state.
        await self._attempt_send_relay()

        return skip_successful # Return whether the skip (Esc Esc) part was successful

    @require_healthy_driver
    async def refresh(self, ctx: Optional[Union[commands.Context, discord.Message, discord.Interaction]] = None) -> bool:
        """
        Executes a "refresh" action by sending the F5 key to the browser.
        This also resets the relay command flag, so /relay is sent on the next skip.
        """
        # --- URL Verification & Redirect (Runs for all calls) ---
        try:
            current_url = await asyncio.to_thread(lambda: self.driver.current_url)

            if self.config.OMEGLE_VIDEO_URL not in current_url:
                logger.warning(f"URL Mismatch: Not on video page (Currently at: {current_url}). Redirecting before refresh.")
                if ctx:
                    msg_content = "Browser is on the wrong page. Redirecting to the stream now..."
                    if isinstance(ctx, discord.Interaction):
                        if ctx.response.is_done(): await ctx.followup.send(msg_content, delete_after=10)
                        else: await ctx.response.send_message(msg_content, delete_after=10)
                    elif hasattr(ctx, 'send'):
                        await ctx.send(msg_content, delete_after=10)
                await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)
                await asyncio.sleep(1.0)

        except Exception as e:
            logger.error(f"Failed during pre-refresh URL check/redirect: {e}", exc_info=True)
            if ctx:
                error_msg = "Error checking browser URL. The browser may be unresponsive."
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done(): await ctx.followup.send(error_msg)
                    else: await ctx.response.send_message(error_msg)
                elif hasattr(ctx, 'send'):
                    await ctx.send(error_msg)
            return False

        # --- F5 Refresh Logic ---
        try:
            logger.info("Selenium: Attempting to refresh the page (F5).")
            # Using driver.refresh() is the standard and most reliable way to simulate F5.
            await asyncio.to_thread(self.driver.refresh)
            
            # On successful refresh, arm the /relay command for the next skip.
            if self.state:
                async with self.state.moderation_lock:
                    self.state.relay_command_sent = False
                logger.info("Relay command armed to be sent on the next skip after refresh.")

            logger.info("Selenium: Page refreshed successfully.")
            await asyncio.sleep(5.3)
            await self.find_and_click_checkbox()
            return True
        except Exception as e:
            logger.error(f"Selenium page refresh failed: {e}")
            if ctx:
                error_msg = "Failed to refresh the browser page."
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done(): await ctx.followup.send(error_msg)
                    else: await ctx.response.send_message(error_msg)
                elif hasattr(ctx, 'send'):
                    await ctx.send(error_msg)
            return False
        
    @require_healthy_driver
    async def report_user(self, ctx: Optional[commands.Context] = None) -> bool:
        """Finds and clicks the report flag icon, confirms the report, and takes a screenshot."""
        try:
            logger.info("Attempting to report user and take screenshot...")

            # --- Screenshot Logic ---
            if self.config.SS_LOCATION:
                try:
                    # Ensure the directory exists
                    os.makedirs(self.config.SS_LOCATION, exist_ok=True)
                    
                    # Create a unique filename with a timestamp and username
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    # Sanitize username for filename
                    sanitized_username = re.sub(r'[\\/*?:"<>|]', "", ctx.author.name)
                    filename = f"report-{timestamp}-{sanitized_username}.jpg"
                    filepath = os.path.join(self.config.SS_LOCATION, filename)
                    
                    def capture_and_save_jpeg():
                        # This CDP command returns a dict {'data': 'base64_string'}
                        screenshot_data = self.driver.execute_cdp_cmd(
                            "Page.captureScreenshot",
                            {"format": "jpeg", "quality": SCREENSHOT_JPEG_QUALITY}
                        )
                        # Decode the base64 string into bytes
                        img_bytes = base64.b64decode(screenshot_data['data'])
                        # Write the bytes to the file
                        with open(filepath, "wb") as f:
                            f.write(img_bytes)
                        return True # Return True on success
                    
                    screenshot_saved = await asyncio.to_thread(capture_and_save_jpeg)
                    if screenshot_saved:
                        logger.info(f"Screenshot (JPEG, Q{SCREENSHOT_JPEG_QUALITY}) saved to: {filepath}")
                        # The line to send the file to ctx is intentionally removed here
                        # as per your request to only save the file locally.
                    else:
                        logger.error("Failed to save screenshot, method returned False.")
                except Exception as ss_e:
                    logger.error(f"Failed to take or send screenshot: {ss_e}", exc_info=True)
                    if ctx:
                        await ctx.send("⚠️ Failed to take screenshot, but proceeding with report.", delete_after=10)
            # --- End Screenshot Logic ---


            # Using a more specific XPath to find the report flag
            report_flag_xpath = "//img[@alt='Report' and contains(@class, 'reportButton')]"
            confirm_button_id = "confirmBan"

            def click_elements():
                # Click the report flag
                report_flag = self.driver.find_element("xpath", report_flag_xpath)
                report_flag.click()
                logger.info("Clicked the report flag icon.")
                
                # Wait for the confirmation dialog to appear
                time.sleep(1)

                # Click the final confirmation button
                confirm_button = self.driver.find_element("id", confirm_button_id)
                confirm_button.click()
                logger.info("Clicked the confirmation report button.")

            await asyncio.to_thread(click_elements)
            
            if ctx:
                await ctx.send("✅ User has been reported.", delete_after=10)
            return True

        except NoSuchElementException as e: # Catch if elements aren't found
             logger.error(f"Failed to find report element: {e.msg}")
             if ctx:
                 await ctx.send("❌ Failed to report user. Could not find report buttons on the page.", delete_after=10)
             return False
        except Exception as e:
            logger.error(f"Failed to report user: {e}", exc_info=True)
            if ctx:
                await ctx.send("❌ Failed to report user. See logs for details.", delete_after=10)
            return False

    async def capture_and_store_screenshot(self) -> None:
        """
        Takes a screenshot of the current browser view and stores it in the in-memory buffer.
        The buffer is capped at 3 screenshots.
        """
        if not await self.is_healthy() or not self.state:
            return

        try:
            def capture_jpeg_bytes():
                # This CDP command returns a dict {'data': 'base64_string'}
                screenshot_data = self.driver.execute_cdp_cmd(
                    "Page.captureScreenshot",
                    {"format": "jpeg", "quality": SCREENSHOT_JPEG_QUALITY}
                )
                # Decode the base64 string into bytes
                return base64.b64decode(screenshot_data['data'])

            # Run the blocking screenshot call in a separate thread.
            screenshot_bytes = await asyncio.to_thread(capture_jpeg_bytes)
            
            async with self.state.screenshot_lock:
                if not hasattr(self.state, 'ban_screenshots'):
                     self.state.ban_screenshots = []
                self.state.ban_screenshots.append((time.time(), screenshot_bytes))
                # Keep the list trimmed to the last 3 screenshots.
                if len(self.state.ban_screenshots) > 3:
                    self.state.ban_screenshots.pop(0)
            
        except Exception as e:
            logger.error(f"Failed to capture and store screenshot for ban buffer: {e}")

    async def check_for_ban(self) -> None:
        """
        [MODIFIED] A passive, periodic check for the browser's URL to manage the bot's ban state.
        It now saves buffered screenshots upon detecting a ban and posts them to Discord.
        """
        if not await self.is_healthy():
            # Don't log here to avoid spam; the require_healthy_driver decorator on commands will handle notifications.
            return

        try:
            current_url = await asyncio.to_thread(lambda: self.driver.current_url)

            # --- BAN DETECTION ---
            async with self.state.moderation_lock:
                if "/ban/" in current_url and not self.state.is_banned:
                    logger.warning(f"Proactive ban check detected a ban! URL: {current_url}.")

                    guild = self.bot.get_guild(self.config.GUILD_ID)
                    streaming_vc = None
                    human_members = []
                    if guild:
                        streaming_vc = guild.get_channel(self.config.STREAMING_VC_ID)
                        if streaming_vc:
                            members_in_vc = streaming_vc.members
                            human_members = [m for m in members_in_vc if not m.bot]

                    try:
                        if guild and streaming_vc:
                            streaming_vc = guild.get_channel(self.config.STREAMING_VC_ID)
                            members_in_vc = streaming_vc.members if streaming_vc else []
                            
                            ban_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                            # Use .bind(BAN_LOG=True) to tag these messages for the ban.log filter
                            logger.bind(BAN_LOG=True).info(f"--- BAN DETECTED at {ban_time} ---")
                            
                            human_members = [m for m in members_in_vc if not m.bot]
                            
                            if human_members:
                                logger.bind(BAN_LOG=True).info(f"Users in streaming VC ({streaming_vc.name}):")
                                for member in human_members:
                                    # Log each user's details
                                    logger.bind(BAN_LOG=True).info(f"  - UserID: {member.id:<20} | Username: {member.name:<32} | DisplayName: {member.display_name}")
                            else:
                                logger.bind(BAN_LOG=True).info("Streaming VC was empty of users at the time of the ban.")
                            
                            logger.bind(BAN_LOG=True).info("--- END OF BAN REPORT ---")
                        else:
                            logger.error("Could not get guild to log users for ban report.")
                    except Exception as ban_log_e:
                        # Log to the main bot.log if the ban.log fails for any reason
                        logger.error(f"Failed to write to ban.log: {ban_log_e}", exc_info=True)

                    if self.config.SS_LOCATION and hasattr(self.state, 'ban_screenshots'):
                        saved_filepaths = []
                        try:
                            # Use a separate lock for screenshots
                            async with self.state.screenshot_lock:
                                screenshots_to_save = self.state.ban_screenshots.copy()
                                self.state.ban_screenshots.clear() # Clear buffer after copying

                            if screenshots_to_save:
                                os.makedirs(self.config.SS_LOCATION, exist_ok=True)
                                ban_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                                
                                # [REMOVED] Block to get VC usernames to prevent long filenames.

                                for i, (capture_time, ss_bytes) in enumerate(screenshots_to_save):
                                    filename = f"ban-{ban_timestamp}-{i + 1}.jpg"
                                    filepath = os.path.join(self.config.SS_LOCATION, filename)
                                    
                                    try:
                                        with open(filepath, "wb") as f:
                                            f.write(ss_bytes)
                                        logger.info(f"Saved pre-ban screenshot to: {filepath}")
                                        saved_filepaths.append(filepath) # Keep track of saved files
                                    except Exception as write_e:
                                        logger.error(f"Failed to write pre-ban screenshot {filename}: {write_e}")
                                
                                logger.info(f"Successfully saved {len(screenshots_to_save)} pre-ban screenshots.")
                                # --- [NEW] Post screenshots to channel ---
                                stats_channel_id = self.config.AUTO_STATS_CHAN or self.config.CHAT_CHANNEL_ID
                                stats_channel = self.bot.get_channel(stats_channel_id)
                                if stats_channel and saved_filepaths:
                                    try:
                                        # --- MODIFICATION: Build new message and remove auto-delete from text message ---
                                        vc_mention = streaming_vc.mention if streaming_vc else f"<#{self.config.STREAMING_VC_ID}>"
                                        user_mentions = " ".join([m.mention for m in human_members]) if human_members else "No users were in the VC."

                                        announcement_msg = (
                                            f"@here The {vc_mention} VC was just banned on Omegle, "
                                            f"the screenshots attached were taken before the ban:\n"
                                            f"{user_mentions}"
                                        )
                                        
                                        # Send announcement without delete_after
                                        await stats_channel.send(announcement_msg)
                                        
                                        files_to_send = [discord.File(fp) for fp in saved_filepaths]
                                        
                                        # Send files, delete after 2 minutes (120 seconds)
                                        await stats_channel.send(files=files_to_send, delete_after=120.0)
                                        
                                        logger.info(f"Posted {len(saved_filepaths)} pre-ban screenshots to channel ID {stats_channel_id} (auto-delete 2m).")
                                        # --- END MODIFICATION ---
                                    except discord.Forbidden:
                                        logger.error(f"Missing permissions to post pre-ban screenshots in channel ID {stats_channel_id}.")
                                    except Exception as post_e:
                                        logger.error(f"Failed to post pre-ban screenshots: {post_e}")
                                elif not stats_channel:
                                    logger.error(f"AUTO_STATS_CHAN (ID: {stats_channel_id}) not found for posting ban screenshots.")
                                # --- End [NEW] ---
                            else:
                                logger.warning("Ban detected, but screenshot buffer was empty.")
                        except Exception as ss_e:
                            logger.error(f"An error occurred while saving/posting pre-ban screenshots: {ss_e}")

                    self.state.is_banned = True # Set state *inside the lock*
                    try:
                        chat_channel = self.bot.get_channel(self.config.CHAT_CHANNEL_ID)
                        if chat_channel:
                            message = (
                                f"@here The Streaming VC Bot just got banned on Omegle - Wait for Host OR use this URL in your browser to pay "
                                f"for an unban - Afterwards, just !skip and it should be unbanned!\n"
                                f"{current_url}"
                            )
                            ban_msg = await chat_channel.send(message)
                            self.state.ban_message_id = ban_msg.id # Store the message ID to delete it later
                            logger.info(f"Sent ban notification (ID: {ban_msg.id}) to channel ID {self.config.CHAT_CHANNEL_ID}.")
                    except Exception as e:
                        logger.error(f"Failed to send ban notification: {e}")

            # --- UNBAN DETECTION ---
            async with self.state.moderation_lock:
                # If we see the main video URL and our state is currently banned, we've just been unbanned.
                if self.config.OMEGLE_VIDEO_URL in current_url and self.state.is_banned:
                    logger.info("Proactive check detected the main video page. Attempting to announce unban.")
                    try:
                        chat_channel = self.bot.get_channel(self.config.CHAT_CHANNEL_ID)
                        if chat_channel:
                            # Delete the old ban message
                            if self.state.ban_message_id:
                                try:
                                    old_ban_msg = await chat_channel.fetch_message(self.state.ban_message_id)
                                    await old_ban_msg.delete()
                                    logger.info(f"Successfully deleted old ban message (ID: {self.state.ban_message_id}).")
                                except discord.NotFound:
                                    logger.warning("Tried to delete old ban message, but it was already gone.")
                                finally:
                                    self.state.ban_message_id = None # Clear ID *inside the lock*

                            message = (
                                f"@here We are now unbanned on Omegle! Feel free to rejoin the <#{self.config.STREAMING_VC_ID}> VC!"
                            )
                            await chat_channel.send(message)
                            logger.info(f"Sent proactive unbanned notification to channel ID {self.config.CHAT_CHANNEL_ID}.")

                            # SUCCESS: Only change the state AFTER the message is sent and old one deleted.
                            self.state.is_banned = False # Set state *inside the lock*
                            logger.info("Bot state successfully updated to unbanned.")

                    except Exception as e:
                        logger.error(f"Failed to send proactive unbanned notification: {e}")

        except UnexpectedAlertPresentException:
            try:
                def handle_alert():
                    alert = self.driver.switch_to.alert
                    alert_text = alert.text
                    alert.dismiss()
                    return alert_text
                alert_text = await asyncio.to_thread(handle_alert)
                logger.warning(f"Handled and dismissed an unexpected browser alert. Text: '{alert_text}'")
            except Exception as alert_e:
                logger.error(f"Tried to handle an unexpected alert, but failed: {alert_e}")
        except Exception as e:
            logger.error(f"Error during passive ban check: {e}")