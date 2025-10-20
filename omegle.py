# omegle.py
# This file contains the OmegleHandler class, which is responsible for all browser
# automation tasks using Selenium. It manages the WebDriver lifecycle and provides
# methods to interact with the Omegle web page.

import asyncio
import os
import time # Added for time.sleep in initialize
from functools import wraps
from typing import Optional, Union

import discord
from discord.ext import commands
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, StaleElementReferenceException, UnexpectedAlertPresentException, NoSuchElementException
from selenium.webdriver.edge.service import Service
from loguru import logger

# Local application imports
import config  # Used for SKIP_COMMAND_KEY if defined
from tools import BotConfig, BotState

# Constants
DRIVER_INIT_RETRIES = 2     # Number of times to retry initializing the WebDriver if it fails.
DRIVER_INIT_DELAY = 5       # Seconds to wait between retry attempts.

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

    async def _attempt_send_relay(self) -> bool:
        """
        Internal helper to send the /relay command.
        It checks if the command has already been sent and updates the state on success.
        Returns True on success, False on failure.
        """
        if not self.state or self.state.relay_command_sent:
            return True  # Already sent, so it's "successful" in a sense.

        logger.info("Attempting to send /relay command...")
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
                self.state.relay_command_sent = True
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
                await asyncio.sleep(3.0) # Wait 3 seconds

                # --- Set Volume ---
                logger.info("Attempting to set initial volume...")
                try:
                    # Set Volume (e.g., to 50%)
                    volume_percentage = 50 # Or get from config if you prefer
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
                    else:
                        logger.warning("Volume slider element not found via script.")

                except Exception as vol_e:
                    logger.error(f"Error during post-navigation volume automation: {vol_e}")

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

    @require_healthy_driver
    async def custom_skip(self, ctx: Optional[commands.Context] = None) -> bool:
        """
        Executes a "skip" action. It first ensures the browser is on the correct URL,
        then simulates key presses to skip the current chat.
        It will also send the /relay command AFTER the first successful skip of a session.
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
            return False

        # --- Standard Skip Logic ---
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
                return False # Return False immediately on other errors

        if not skip_successful:
            logger.error("Failed to execute custom skip after multiple retries due to stale elements.")
            if ctx: await ctx.send("Failed to execute skip command after multiple retries.")
            return False

        # --- Send /relay (or retry if startup send failed) ---
        # This helper has a built-in check so it only runs once.
        await self._attempt_send_relay()

        return True # Return True as the skip itself was successful

    @require_healthy_driver
    async def refresh(self, ctx: Optional[Union[commands.Context, discord.Message, discord.Interaction]] = None) -> bool:
        """
        Executes a "refresh" action. It first ensures the browser is on the correct URL,
        then sends the Escape key 4 times to reset the state on the target page.
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
                await asyncio.sleep(2.0)

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

        # --- Standard Refresh Logic ---
        for attempt in range(3):
            try:
                for i in range(4):
                    script = f"""
                    var evt = new KeyboardEvent('keydown', {{
                        bubbles: true, cancelable: true, key: 'Escape', code: 'Escape'
                    }});
                    document.dispatchEvent(evt);
                    """
                    await asyncio.to_thread(self.driver.execute_script, script)
                    logger.info(f"Selenium: Sent Escape key event to page (press {i+1}/4).")
                    if i < 3:
                        await asyncio.sleep(0.1) # Small delay between keys

                logger.info("Selenium: Successfully sent 4 escape key presses for refresh command.")
                return True
            except StaleElementReferenceException:
                logger.warning(f"StaleElementReferenceException on refresh attempt {attempt + 1}. Retrying...")
                await asyncio.sleep(0.5)
                continue
            except Exception as e:
                logger.error(f"Selenium refresh (escape key press) failed: {e}")
                if ctx:
                    error_msg = "Failed to process refresh command in browser."
                    if isinstance(ctx, discord.Interaction):
                        if ctx.response.is_done(): await ctx.followup.send(error_msg)
                        else: await ctx.response.send_message(error_msg)
                    elif hasattr(ctx, 'send'):
                        await ctx.send(error_msg)
                return False

        logger.error("Failed to execute refresh after multiple retries due to stale elements.")
        if ctx:
            error_msg = "Failed to execute refresh command after multiple retries."
            if isinstance(ctx, discord.Interaction):
                if ctx.response.is_done(): await ctx.followup.send(error_msg)
                else: await ctx.response.send_message(error_msg)
            elif hasattr(ctx, 'send'):
                await ctx.send(error_msg)
        return False
        
    @require_healthy_driver
    async def report_user(self, ctx: Optional[commands.Context] = None) -> bool:
        """Finds and clicks the report flag icon, then confirms the report."""
        try:
            logger.info("Attempting to report user...")

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

    async def check_for_ban(self) -> None:
        """
        [NEW] A passive, periodic check for the browser's URL to manage the bot's ban state.
        It does not navigate or alter the browser state, only reads it.
        """
        if not await self.is_healthy():
            # Don't log here to avoid spam; the require_healthy_driver decorator on commands will handle notifications.
            return

        try:
            current_url = await asyncio.to_thread(lambda: self.driver.current_url)

            # --- BAN DETECTION ---
            # If we see a ban URL and our state is currently unbanned, we've just been banned.
            if "/ban/" in current_url and not self.state.is_banned:
                logger.warning(f"Proactive ban check detected a ban! URL: {current_url}.")
                self.state.is_banned = True # Set state to prevent re-announcements.
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
            # If we see the main video URL and our state is currently banned, we've just been unbanned.
            elif self.config.OMEGLE_VIDEO_URL in current_url and self.state.is_banned:
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
                                self.state.ban_message_id = None

                        message = (
                            f"@here We are now unbanned on Omegle! Feel free to rejoin the <#{self.config.STREAMING_VC_ID}> VC!"
                        )
                        await chat_channel.send(message)
                        logger.info(f"Sent proactive unbanned notification to channel ID {self.config.CHAT_CHANNEL_ID}.")

                        # SUCCESS: Only change the state AFTER the message is sent.
                        self.state.is_banned = False
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