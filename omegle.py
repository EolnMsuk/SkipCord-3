# omegle.py

import asyncio
import os
import re
import base64
import time
import random
import atexit  # <--- Added atexit
from datetime import datetime, timezone
from functools import wraps
from typing import Optional, Union, List, Tuple

import discord
from discord.ext import commands
from selenium import webdriver
from selenium.common.exceptions import (
    WebDriverException,
    StaleElementReferenceException,
    UnexpectedAlertPresentException,
    NoSuchElementException,
)
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys 
from loguru import logger

import config
from tools import BotConfig, BotState

# --- Constants ---

# Number of retries for initializing the Selenium driver if it fails
DRIVER_INIT_RETRIES = 3
# Delay in seconds between driver initialization retries
DRIVER_INIT_DELAY = 5
# Quality level (1-100) for JPEG screenshots
SCREENSHOT_JPEG_QUALITY = 60


# --- Decorator ---

def require_healthy_driver(func):
    """
    Decorator to ensure the Selenium driver is healthy before running a method.

    This decorator checks `is_healthy()` before executing the wrapped function.
    If the driver is unhealthy (e.g., browser crashed, session lost), it
    attempts to automatically relaunch the browser by calling `self.initialize()`.

    If the relaunch is successful, it retries the original function.
    If the relaunch fails, it logs a critical error and informs the user.
    """

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        """Wrapper function that performs health checks and retries."""

        def find_context() -> Optional[
            Union[commands.Context, discord.Message, discord.Interaction]
        ]:
            """Helper to find the Discord context from args or kwargs."""
            ctx = None
            if args:
                ctx_candidate = args[0]
                if isinstance(
                    ctx_candidate,
                    (commands.Context, discord.Message, discord.Interaction),
                ):
                    return ctx_candidate
            if "ctx" in kwargs:
                ctx_candidate = kwargs["ctx"]
                if isinstance(
                    ctx_candidate,
                    (commands.Context, discord.Message, discord.Interaction),
                ):
                    return ctx_candidate
            return None

        async def send_to_context(ctx, msg, ephemeral=False):
            """Helper to send a message to the found context."""
            if not ctx:
                return
            try:
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done():
                        await ctx.followup.send(msg, ephemeral=ephemeral)
                    else:
                        await ctx.response.send_message(msg, ephemeral=ephemeral)
                elif hasattr(ctx, "send"):
                    await ctx.send(msg)
            except Exception as e:
                logger.error(f"Failed to send message to context: {e}")

        try:
            # First check: is the driver healthy?
            if not await self.is_healthy():
                async with self._init_lock:
                    # Double-check inside lock to prevent race conditions
                    if not await self.is_healthy():
                        logger.warning(
                            "Driver is unhealthy. Attempting to relaunch the browser..."
                        )
                        ctx = find_context()
                        await send_to_context(
                            ctx, "Browser connection lost. Attempting to relaunch..."
                        )

                        # Attempt to re-initialize
                        if not await self.initialize():
                            logger.critical(
                                "Failed to relaunch the browser after retries. Manual restart required."
                            )
                            await send_to_context(
                                ctx,
                                "Failed to relaunch the browser. Please restart the bot manually.",
                                ephemeral=True,
                            )
                            return False

                        logger.info("Browser relaunched successfully.")
                        await send_to_context(
                            ctx, "Browser has been successfully relaunched."
                        )

            # If healthy, run the original function
            return await func(self, *args, **kwargs)

        except (WebDriverException, StaleElementReferenceException) as e:
            # Handle exceptions that indicate a dead or stale driver session
            ctx = find_context()
            if "invalid session id" in str(e):
                logger.warning(
                    f"Driver session invalid. Attempting to relaunch... (Error: {e.msg.splitlines()[0]})"
                )
            else:
                logger.error(
                    f"WebDriverException in {func.__name__}: {e}", exc_info=True
                )

            await send_to_context(
                ctx, "Browser connection lost. Attempting to relaunch..."
            )

            # Re-initialize the driver
            async with self._init_lock:
                if not await self.initialize():
                    logger.critical(
                        "Failed to relaunch the browser after retries. Manual restart required."
                    )
                    await send_to_context(
                        ctx,
                        "Failed to relaunch the browser. Please restart the bot manually.",
                        ephemeral=True,
                    )
                    return False

                logger.info("Browser relaunched successfully.")
                await send_to_context(
                    ctx, "Browser has been successfully relaunched."
                )

                # Retry the command one more time after relaunch
                logger.info(
                    f"Retrying command '{func.__name__}' after relaunch."
                )
                try:
                    return await func(self, *args, **kwargs)
                except Exception as retry_e:
                    logger.error(
                        f"Command '{func.__name__}' failed even after relaunch: {retry_e}",
                        exc_info=True,
                    )
                    await send_to_context(
                        ctx,
                        f"Command {func.__name__} failed after relaunch. Please try again.",
                        ephemeral=True,
                    )
                    return False

    return wrapper


# --- Main Class ---

class OmegleHandler:
    """
    Manages all Selenium browser interactions for the Omegle stream.

    This class handles initializing the Edge browser, navigating to the Omegle
    URL, and programmatically performing actions like skipping, refreshing,
    reporting, and checking for bans.
    """

    def __init__(self, bot: commands.Bot, bot_config: BotConfig):
        """
        Initializes the OmegleHandler.

        Args:
            bot: The main discord.Bot instance.
            bot_config: The loaded BotConfig object.
        """
        self.bot = bot
        self.config = bot_config
        self.driver: Optional[webdriver.Edge] = None
        self._driver_initialized = False  # Flag to track if driver is ready
        self.state: Optional[BotState] = None  # To be attached by the main bot
        self._init_lock = asyncio.Lock()  # Lock to prevent concurrent initializations

    def _sync_force_close(self):
        """
        Synchronous cleanup method for atexit. 
        Forces the driver to quit if it's still active on interpreter shutdown.
        """
        if self.driver:
            try:
                # Direct call to quit() since we are shutting down and async loop might be dead
                self.driver.quit()
                logger.info("atexit: Selenium driver force-closed.")
            except Exception as e:
                # Use print because logger might be dead during atexit
                print(f"atexit: Error closing driver: {e}")

    async def _set_volume(self, volume_percentage: int) -> bool:
        """
        Executes JavaScript to set the volume slider on the Omegle page.

        Args:
            volume_percentage: The desired volume (0-100).

        Returns:
            True if the script executed successfully, False otherwise.
        """
        # Clamp volume just in case
        volume_percentage = max(0, min(100, volume_percentage))
        logger.info(f"Attempting to set volume to {volume_percentage}%...")
        try:
            # JavaScript to find the volume slider and set its value
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
            volume_set = await asyncio.to_thread(
                self.driver.execute_script, set_volume_script
            )
            if volume_set:
                logger.info(
                    f"Successfully executed script to set volume to {volume_percentage}%."
                )
                return True
            else:
                logger.warning("Volume slider element not found via script.")
                return False
        except Exception as e:
            logger.error(f"Error during volume automation: {e}")
            return False

    async def _attempt_send_relay(self) -> bool:
        """
        Attempts to send the '/relay' command and set volume based on config.
        Updated for Umingle/Ome.tv compatibility using ENTER key instead of clicking buttons.
        """
        async with self.state.moderation_lock:
            if not self.state or self.state.relay_command_sent:
                return True
            
            self.state.relay_command_sent = True 
            logger.info("Processing auto-relay and auto-volume checks...")

        # --- 1. Handle Auto Relay ---
        if not self.config.AUTO_RELAY:
            logger.info("AUTO_RELAY is False. Skipping /relay command.")
        else:
            logger.info("AUTO_RELAY is True. Attempting to send /relay command...")
            try:
                # Common selectors for Omegle clones (Umingle, Uhmegle, Ome.tv)
                input_selectors = [
                    "textarea.chat-msg",       # Common on Ome.tv/Umingle
                    "textarea.messageInput",   # Common on Uhmegle
                    "textarea[placeholder='Type your message...']",
                    "textarea"                 # Fallback generic
                ]

                def send_relay_command():
                    """Blocking function to interact with Selenium elements."""
                    try:
                        time.sleep(1.0)  # Wait for elements to be stable
                        
                        chat_input = None
                        # Try finding the input box using multiple known selectors
                        for selector in input_selectors:
                            try:
                                chat_input = self.driver.find_element(By.CSS_SELECTOR, selector)
                                if chat_input.is_displayed():
                                    break
                            except NoSuchElementException:
                                continue
                        
                        if not chat_input:
                            logger.warning("Could not find chat input text area.")
                            return False

                        # Type command and press ENTER
                        chat_input.clear()
                        chat_input.send_keys("/relay")
                        time.sleep(0.1)
                        chat_input.send_keys(Keys.RETURN)
                        return True

                    except Exception as e:
                        logger.warning(
                            f"Error interacting with chat elements: {e}"
                        )
                        return False

                relay_sent = await asyncio.to_thread(send_relay_command)

                if relay_sent:
                    logger.info("Successfully sent /relay command via Return key.")
                else:
                    logger.warning("Failed to send /relay command.")

            except Exception as e:
                logger.error(
                    f"An unexpected error occurred when trying to send /relay: {e}"
                )

        # --- 2. Handle Auto Volume ---
        if self.config.AUTO_OMEGLE_VOL:
            logger.info(f"AUTO_OMEGLE_VOL is True. Setting volume to {self.config.OMEGLE_VOL}%.")
            await self._set_volume(volume_percentage=self.config.OMEGLE_VOL)
        else:
            logger.info("AUTO_OMEGLE_VOL is False. Skipping volume set.")

        return True

    async def _perform_skip_keys(self, ctx: Optional[commands.Context] = None) -> bool:
        """Helper to press the skip keys (Esc) on the page."""
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
                        await asyncio.sleep(1)
                skip_successful = True
                break
            except StaleElementReferenceException:
                logger.warning(f"StaleElementReferenceException on skip attempt {attempt + 1}. Retrying...")
                await asyncio.sleep(0.5)
                continue
            except Exception as e:
                logger.error(f"Selenium custom skip failed: {e}")
                if ctx:
                    await ctx.send("Failed to execute skip command in browser.")
                skip_successful = False
                break
        return skip_successful

    async def _is_streaming_vc_active(self) -> bool:
        """
        Checks if the main streaming VC has active users with cameras on.

        Returns:
            True if at least one non-bot, non-owner user has their camera on.
        """
        try:
            guild = self.bot.get_guild(self.config.GUILD_ID)
            if not guild:
                logger.warning("Could not check VC status: Guild not found.")
                return False

            streaming_vc = guild.get_channel(self.config.STREAMING_VC_ID)
            if not streaming_vc or not isinstance(streaming_vc, discord.VoiceChannel):
                logger.warning(
                    "Could not check VC status: Streaming VC not found or invalid."
                )
                return False

            # Check members in the VC
            for member in streaming_vc.members:
                # Ignore bots and configured allowed users
                if member.bot or member.id in self.config.ALLOWED_USERS:
                    continue
                # Check for camera on
                if member.voice and member.voice.self_video:
                    logger.info("Active user with camera on detected in streaming VC.")
                    return True

            logger.info("Streaming VC has no active users with camera on.")
            return False
        except Exception as e:
            logger.error(f"Error checking VC status: {e}")
            return False

    async def initialize(self) -> bool:
        """
        Initializes the Selenium Edge driver and opens the Omegle page.

        Handles driver version management, retries, and setting browser
        options for stealth and stability. Also restores window position.

        Returns:
            True if initialization was successful, False otherwise.
        """
        for attempt in range(DRIVER_INIT_RETRIES):
            try:
                # Clean up old driver if it exists
                if self.driver is not None:
                    await self.close()

                # --- Configure Edge Options ---
                options = webdriver.EdgeOptions()
                options.add_argument(f"user-data-dir={self.config.EDGE_USER_DATA_DIR}")
                options.add_argument("--ignore-certificate-errors")
                options.add_argument("--allow-running-insecure-content")
                options.add_argument("--log-level=3")  # Suppress console logs
                options.add_argument(
                    "--disable-blink-features=AutomationControlled"
                )  # Stealth
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-infobars")
                options.add_argument("--disable-popup-blocking")
                options.add_experimental_option(
                    "excludeSwitches", ["enable-automation", "enable-logging"]
                )
                options.add_experimental_option("useAutomationExtension", False)

                # --- Initialize Driver (Auto or Manual Path) ---
                try:
                    logger.info("Initializing Selenium with automatic driver management...")
                    # Try with built-in driver manager first
                    self.driver = await asyncio.to_thread(
                        webdriver.Edge, options=options
                    )
                    logger.info("Automatic driver management successful.")
                    
                    # --- Register atexit cleanup ---
                    atexit.register(self._sync_force_close)

                except WebDriverException as auto_e:
                    logger.warning(f"Automatic driver management failed: {auto_e}")
                    # Fallback to user-provided path if available
                    if (
                        self.config.EDGE_DRIVER_PATH
                        and os.path.exists(self.config.EDGE_DRIVER_PATH)
                    ):
                        logger.info(
                            f"Attempting fallback with specified driver path: {self.config.EDGE_DRIVER_PATH}"
                        )
                        try:
                            service = Service(
                                executable_path=self.config.EDGE_DRIVER_PATH
                            )
                            self.driver = await asyncio.to_thread(
                                webdriver.Edge, service=service, options=options
                            )
                            logger.info("Fallback driver path successful.")
                            
                            # --- Register atexit cleanup (Fallback) ---
                            atexit.register(self._sync_force_close)

                        except Exception as fallback_e:
                            logger.error(
                                f"Fallback driver path also failed: {fallback_e}"
                            )
                            raise fallback_e
                    else:
                        logger.warning(
                            "No fallback driver path specified or path is invalid. Retrying with automatic management."
                        )
                        raise auto_e

                # --- Apply Stealth & Window Geometry ---
                # Inject JS to hide the 'webdriver' flag
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
                await asyncio.to_thread(
                    self.driver.execute_cdp_cmd,
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": stealth_script},
                )

                # Restore window size/position if saved
                if (
                    self.state
                    and self.state.window_size
                    and self.state.window_position
                ):
                    try:
                        logger.info(
                            f"Restoring window to size: {self.state.window_size} and position: {self.state.window_position}"
                        )

                        def set_geometry():
                            self.driver.set_window_size(
                                self.state.window_size["width"],
                                self.state.window_size["height"],
                            )
                            self.driver.set_window_position(
                                self.state.window_position["x"],
                                self.state.window_position["y"],
                            )

                        await asyncio.to_thread(set_geometry)
                    except Exception as geo_e:
                        logger.error(f"Failed to restore window geometry: {geo_e}")

                # --- Navigate and Perform Initial Actions ---
                logger.info(f"Navigating to {self.config.OMEGLE_VIDEO_URL}...")
                await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)
                
                # Set driver as initialized *before* calling any wrapped methods
                self._driver_initialized = True
                logger.info("Driver initialized, running startup refresh logic...")

                # Run the !refresh logic on startup. This includes the delay and the checkbox click logic.
                await self.refresh(ctx=None)

                # --- Final State Setup ---
                if self.state:
                    self.state.relay_command_sent = False

                    # Always log this message, as we always wait for skip
                    logger.info(
                        "Startup refresh complete. Relay is armed for next user !skip."
                    )
                else:
                    logger.warning(
                        "Bot state not attached to omegle_handler, cannot send /relay on startup."
                    )

                logger.info("Selenium initialization and startup refresh complete.")
                return True  # Success

            except Exception as e:
                logger.error(
                    f"Selenium initialization attempt {attempt + 1} failed: {e}"
                )
                if "This version of Microsoft Edge Driver only supports" in str(e):
                    logger.critical(
                        "CRITICAL: WebDriver version mismatch. Please update Edge browser or check for driver issues."
                    )
                if attempt < DRIVER_INIT_RETRIES - 1:
                    await asyncio.sleep(DRIVER_INIT_DELAY)

        # If loop finishes without returning True
        logger.critical("Failed to initialize Selenium driver after retries.")
        self._driver_initialized = False
        return False

    async def is_healthy(self) -> bool:
        """
        Checks if the driver is initialized and the browser is responsive.

        Returns:
            True if the driver is considered healthy, False otherwise.
        """
        if not self._driver_initialized or self.driver is None:
            return False
        if self._init_lock.locked():
            # If init is in progress, assume it will be healthy
            return True
        try:
            # A simple, non-blocking command to check session validity
            await asyncio.to_thread(lambda: self.driver.current_url)
            return True
        except Exception:
            return False

    async def get_window_geometry(self) -> Optional[tuple[dict, dict]]:
        """
        Gets the current browser window size and position.

        Returns:
            A tuple of (size, position) dicts, or None on failure.
        """
        if not await self.is_healthy():
            return None
        try:
            def get_geo():
                size = self.driver.get_window_size()
                position = self.driver.get_window_position()
                return (size, position)

            size, position = await asyncio.to_thread(get_geo)
            return (size, position)
        except Exception as e:
            logger.error(f"Could not get window geometry: {e}")
            return None

    async def close(self) -> None:
        """Shuts down the Selenium driver and browser."""
        # --- Unregister atexit to prevent double execution ---
        try:
            atexit.unregister(self._sync_force_close)
        except Exception:
            pass

        if self.driver is not None:
            try:
                await asyncio.to_thread(self.driver.quit)
                logger.info("Selenium driver closed.")
            except Exception as e:
                logger.error(f"Error closing Selenium driver: {e}")
            finally:
                self.driver = None
                self._driver_initialized = False

    @require_healthy_driver
    async def find_and_click_checkbox(self) -> bool:
        """
        Finds and clicks ANY unselected checkboxes on the page.
        
        Includes logic to handle Cloudflare 'Verify you are human' widgets 
        by detecting and switching into challenge iframes.
        """
        try:
            def perform_checkbox_click():
                clicked_something = False

                # --- 1. CLOUDFLARE / IFRAME HANDLING ---
                try:
                    # Look for iframes that might contain Cloudflare challenges
                    # Common ID is 'turnstile-wrapper' or src containing 'cloudflare'
                    iframes = self.driver.find_elements(By.XPATH, "//iframe[contains(@src, 'cloudflare') or contains(@title, 'challenge')]")
                    
                    for frame in iframes:
                        try:
                            self.driver.switch_to.frame(frame)
                            logger.info("Switched to potential Cloudflare iframe.")
                            
                            # Attempt to find the specific Cloudflare checkbox/body
                            # Cloudflare often uses a specific shadow element, but a general click on the body or input usually triggers it
                            cf_checkbox = WebDriverWait(self.driver, 2).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='checkbox'], label.ctp-checkbox-label, div.ctp-checkbox-container"))
                            )
                            
                            if cf_checkbox:
                                logger.info("Found element inside Cloudflare frame. Clicking...")
                                cf_checkbox.click()
                                clicked_something = True
                                time.sleep(2.0) # Wait for Cloudflare to process the click
                                
                        except Exception:
                            # If we can't find it or click it in this frame, just continue
                            pass
                        finally:
                            # ALWAYS switch back to default content
                            self.driver.switch_to.default_content()
                
                except Exception as cf_e:
                    logger.debug(f"Cloudflare check skipped or failed: {cf_e}")
                    self.driver.switch_to.default_content()

                # --- 2. STANDARD PAGE CHECKBOXES ---
                try:
                    # Wait for any standard checkboxes to be present on the main page
                    checkboxes = WebDriverWait(self.driver, 3).until(
                        EC.presence_of_all_elements_located(
                            (By.CSS_SELECTOR, 'input[type="checkbox"]')
                        )
                    )
                    
                    if checkboxes:
                        logger.info(f"Found {len(checkboxes)} standard checkbox(es).")
                        for checkbox in checkboxes:
                            try:
                                if not checkbox.is_selected():
                                    if clicked_something:
                                        time.sleep(random.uniform(0.4, 0.9))
                                    
                                    logger.info("Unchecked box found. Attempting JavaScript click...")
                                    self.driver.execute_script("arguments[0].click();", checkbox)
                                    clicked_something = True
                            except StaleElementReferenceException:
                                continue
                except Exception:
                    # No standard checkboxes found (timeout), which is fine
                    pass

                return clicked_something

            clicked = await asyncio.to_thread(perform_checkbox_click)
            if clicked:
                logger.info("Successfully handled checkboxes (Cloudflare or Standard).")
            return clicked 
            
        except Exception as e:
            logger.error(f"An error occurred in find_and_click_checkbox: {e}", exc_info=True)
            return False

    @require_healthy_driver
    async def custom_skip(self, ctx: Optional[commands.Context] = None) -> bool:
        """
        Performs the 'skip' action mechanically.
        If URL mismatch: Navigates -> Waits 5.3s -> Clicks Checkboxes -> (If Clicked) Waits 5.3s.
        THEN: Waits 0.5s -> Skips -> Relays immediately.
        """
        current_url = await asyncio.to_thread(lambda: self.driver.current_url)
        video_url = self.config.OMEGLE_VIDEO_URL

        # 1. Ensure we are on the correct video URL
        if video_url not in current_url:
            logger.info("Not on video URL. Navigating to video URL...")
            await asyncio.to_thread(self.driver.get, video_url)
            
            # --- DELAY 1: Wait 5.3 seconds BEFORE checking for checkboxes ---
            logger.info("Waiting 5.3s for load...")
            await asyncio.sleep(5.3)

            # Scan for and click checkboxes
            checkboxes_clicked = await self.find_and_click_checkbox()

            # --- DELAY 2: Wait 5.3 seconds AFTER clicking checkboxes (Conditional) ---
            # This block is skipped entirely if no checkboxes were found/clicked
            if checkboxes_clicked:
                logger.info("Checkboxes detected and clicked. Waiting an additional 5.3s...")
                await asyncio.sleep(5.3)
        
        # 2. DELAY 3: User requested 0.5s delay BEFORE the skip keys
        await asyncio.sleep(0.5)

        # 3. Perform Relay/Vol Adjust BEFORE skipping (User Request)
        # We run this unconditionally before the keys to ensure volume is set
        await self._attempt_send_relay()

        # 4. Perform Skip Keys (Esc Esc) - This opens the chat box/skips
        skip_successful = await self._perform_skip_keys(ctx)
        
        return skip_successful

    @require_healthy_driver
    async def refresh(
        self,
        ctx: Optional[Union[commands.Context, discord.Message, discord.Interaction]] = None,
    ) -> bool:
        """
        Refreshes the browser page mechanically.
        Checks for bans are now handled solely by the periodic check_for_ban task.
        """
        video_url = self.config.OMEGLE_VIDEO_URL

        # 1. Force Navigation to video_url
        logger.info(f"Navigating/Refreshing to {video_url}...")
        await asyncio.to_thread(self.driver.get, video_url)
        
        # 2. Wait 5.34 seconds BEFORE checking for checkboxes
        logger.info("Waiting 5.34s for checkboxes/scripts to load...")
        await asyncio.sleep(5.34)
        
        # 3. Click Checkboxes (Always)
        await self.find_and_click_checkbox()
        
        # Reset relay flag so next skip triggers it
        if self.state:
            async with self.state.moderation_lock:
                self.state.relay_command_sent = False
        return True

    @require_healthy_driver
    async def report_user(self, ctx: Optional[commands.Context] = None) -> bool:
        """
        Reports the current Omegle user and takes a screenshot.

        Args:
            ctx: The Discord context, for sending status messages.

        Returns:
            True if the report was successful, False otherwise.
        """
        try:
            logger.info("Attempting to report user and take screenshot...")

            # --- Take Screenshot ---
            if self.config.SS_LOCATION:
                try:
                    os.makedirs(self.config.SS_LOCATION, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    sanitized_username = re.sub(
                        r'[\\/*?:"<>|]', "", ctx.author.name
                    )
                    filename = f"report-{timestamp}-{sanitized_username}.jpg"
                    filepath = os.path.join(self.config.SS_LOCATION, filename)

                    def capture_and_save_jpeg():
                        # Use CDP to capture screenshot as JPEG for smaller file size
                        screenshot_data = self.driver.execute_cdp_cmd(
                            "Page.captureScreenshot",
                            {"format": "jpeg", "quality": SCREENSHOT_JPEG_QUALITY},
                        )
                        # Decode base64 data
                        img_bytes = base64.b64decode(screenshot_data["data"])
                        with open(filepath, "wb") as f:
                            f.write(img_bytes)
                        return True

                    screenshot_saved = await asyncio.to_thread(capture_and_save_jpeg)
                    if screenshot_saved:
                        logger.info(
                            f"Screenshot (JPEG, Q{SCREENSHOT_JPEG_QUALITY}) saved to: {filepath}"
                        )
                    else:
                        logger.error("Failed to save screenshot, method returned False.")
                except Exception as ss_e:
                    logger.error(
                        f"Failed to take or send screenshot: {ss_e}", exc_info=True
                    )
                    if ctx:
                        await ctx.send(
                            "⚠️ Failed to take screenshot, but proceeding with report.",
                            delete_after=10,
                        )

            # --- Click Report Buttons ---
            report_flag_xpath = (
                "//img[@alt='Report' and contains(@class, 'reportButton')]"
            )
            confirm_button_id = "confirmBan"

            def click_elements():
                """Blocking function to find and click report elements."""
                report_flag = self.driver.find_element("xpath", report_flag_xpath)
                report_flag.click()
                logger.info("Clicked the report flag icon.")
                time.sleep(1)  # Wait for confirmation modal
                confirm_button = self.driver.find_element("id", confirm_button_id)
                confirm_button.click()
                logger.info("Clicked the confirmation report button.")

            await asyncio.to_thread(click_elements)

            if ctx:
                await ctx.send("✅ User has been reported.", delete_after=10)
            return True

        except NoSuchElementException as e:
            logger.error(f"Failed to find report element: {e.msg}")
            if ctx:
                await ctx.send(
                    "❌ Failed to report user. Could not find report buttons on the page.",
                    delete_after=10,
                )
            return False
        except Exception as e:
            logger.error(f"Failed to report user: {e}", exc_info=True)
            if ctx:
                await ctx.send(
                    "❌ Failed to report user. See logs for details.", delete_after=10
                )
            return False

    @require_healthy_driver
    async def skip_from_hotkey(self) -> bool:
        """
        Wrapper function to allow `custom_skip` to be called from a hotkey.
        """
        logger.info("Global hotkey skip received. Executing custom_skip...")
        return await self.custom_skip(ctx=None)

    async def capture_and_store_screenshot(self) -> None:
        """
        Captures a screenshot and stores it in the state's `ban_screenshots` buffer.
        This buffer is used to save evidence if a ban is detected.
        """
        if not await self.is_healthy():
            return

        try:
            def capture_jpeg_bytes():
                """Blocking function to capture screenshot data."""
                screenshot_data = self.driver.execute_cdp_cmd(
                    "Page.captureScreenshot",
                    {"format": "jpeg", "quality": SCREENSHOT_JPEG_QUALITY},
                )
                return base64.b64decode(screenshot_data["data"])

            screenshot_bytes = await asyncio.to_thread(capture_jpeg_bytes)

            async with self.state.screenshot_lock:
                if not hasattr(self.state, "ban_screenshots"):
                    self.state.ban_screenshots = []
                # Add screenshot with a timestamp
                self.state.ban_screenshots.append((time.time(), screenshot_bytes))
                # Keep the buffer size limited (e.g., last 3 screenshots)
                if len(self.state.ban_screenshots) > 3:
                    self.state.ban_screenshots.pop(0)

        except Exception as e:
            logger.error(
                f"Failed to capture and store screenshot for ban buffer: {e}",
                exc_info=True
            )

    async def check_for_ban(self) -> None:
        """
        Periodically checks the browser's URL to see if a ban has occurred
        or if a previous ban has been resolved.
        """
        if not await self.is_healthy():
            return

        try:
            current_url = await asyncio.to_thread(lambda: self.driver.current_url)

            # --- BAN DETECTION ---
            async with self.state.moderation_lock:
                if "/ban/" in current_url and (not self.state.is_banned):
                    logger.warning(
                        f"Proactive ban check detected a ban! URL: {current_url}."
                    )

                    # --- Log Users in VC at Time of Ban ---
                    guild = self.bot.get_guild(self.config.GUILD_ID)
                    streaming_vc = None
                    human_members = []
                    if guild:
                        streaming_vc = guild.get_channel(self.config.STREAMING_VC_ID)
                        if streaming_vc:
                            members_in_vc = streaming_vc.members
                            human_members = [
                                m for m in members_in_vc if not m.bot
                            ]
                    try:
                        if guild and streaming_vc:
                            ban_time = datetime.now(timezone.utc).strftime(
                                "%Y-%m-%d %H:%M:%S UTC"
                            )
                            logger.bind(BAN_LOG=True).info(
                                f"--- BAN DETECTED at {ban_time} ---"
                            )
                            if human_members:
                                logger.bind(BAN_LOG=True).info(
                                    f"Users in streaming VC ({streaming_vc.name}):"
                                )
                                for member in human_members:
                                    logger.bind(BAN_LOG=True).info(
                                        f"  - UserID: {member.id:<20} | Username: {member.name:<32} | DisplayName: {member.display_name}"
                                    )
                            else:
                                logger.bind(BAN_LOG=True).info(
                                    "Streaming VC was empty of users at the time of the ban."
                                )
                            logger.bind(BAN_LOG=True).info("--- END OF BAN REPORT ---")
                        else:
                            logger.error(
                                "Could not get guild to log users for ban report."
                            )
                    except Exception as ban_log_e:
                        logger.error(
                            f"Failed to write to ban.log: {ban_log_e}", exc_info=True
                        )

                    # --- Save and Post Pre-Ban Screenshots ---
                    if self.config.SS_LOCATION and hasattr(
                        self.state, "ban_screenshots"
                    ):
                        saved_filepaths = []
                        try:
                            async with self.state.screenshot_lock:
                                screenshots_to_save = self.state.ban_screenshots.copy()
                                self.state.ban_screenshots.clear()

                            if screenshots_to_save:
                                os.makedirs(self.config.SS_LOCATION, exist_ok=True)
                                ban_timestamp = datetime.now().strftime(
                                    "%Y-%m-%d_%H-%M-%S"
                                )
                                def save_images_sync(screenshots):
                                    saved_paths = []
                                    os.makedirs(self.config.SS_LOCATION, exist_ok=True)
                                    # Calculate timestamp once for the batch
                                    ban_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                                    
                                    for idx, (cap_time, data_bytes) in enumerate(screenshots):
                                        fname = f"ban-{ban_ts}-{idx + 1}.jpg"
                                        fpath = os.path.join(self.config.SS_LOCATION, fname)
                                        try:
                                            with open(fpath, "wb") as f:
                                                f.write(data_bytes)
                                            logger.info(f"Saved pre-ban screenshot to: {fpath}")
                                            saved_paths.append(fpath)
                                        except Exception as write_err:
                                            logger.error(f"Failed to write screenshot {fname}: {write_err}")
                                    return saved_paths

                                # Run the save operation in a thread
                                saved_filepaths = await asyncio.to_thread(save_images_sync, screenshots_to_save)

                                logger.info(
                                    f"Successfully saved {len(screenshots_to_save)} pre-ban screenshots."
                                )

                                # Post screenshots to stats/chat channel
                                stats_channel_id = (
                                    self.config.AUTO_STATS_CHAN
                                    or self.config.CHAT_CHANNEL_ID
                                )
                                stats_channel = self.bot.get_channel(stats_channel_id)
                                if stats_channel and saved_filepaths:
                                    try:
                                        vc_mention = (
                                            streaming_vc.mention
                                            if streaming_vc
                                            else f"<#{self.config.STREAMING_VC_ID}>"
                                        )
                                        user_mentions = (
                                            " ".join(
                                                [m.mention for m in human_members]
                                            )
                                            if human_members
                                            else "No users were in the VC."
                                        )
                                        announcement_msg = f"@here The {vc_mention} VC was just banned on Omegle\nUsers in chat when banned: {user_mentions}"
                                        await stats_channel.send(
                                            announcement_msg, delete_after=120.0
                                        )
                                        files_to_send = [
                                            discord.File(fp)
                                            for fp in saved_filepaths
                                        ]
                                        await stats_channel.send(
                                            files=files_to_send, delete_after=120.0
                                        )
                                        logger.info(
                                            f"Posted {len(saved_filepaths)} pre-ban screenshots to channel ID {stats_channel_id} (auto-delete 2m)."
                                        )
                                    except discord.Forbidden:
                                        logger.error(
                                            f"Missing permissions to post pre-ban screenshots in channel ID {stats_channel_id}."
                                        )
                                    except Exception as post_e:
                                        logger.error(
                                            f"Failed to post pre-ban screenshots: {e}"
                                        )
                                elif not stats_channel:
                                    logger.error(
                                        f"AUTO_STATS_CHAN (ID: {stats_channel_id}) not found for posting ban screenshots."
                                    )
                            else:
                                logger.warning(
                                    "Ban detected, but screenshot buffer was empty."
                                )
                        except Exception as ss_e:
                            logger.error(
                                f"An error occurred while saving/posting pre-ban screenshots: {ss_e}"
                            )

                    # --- Update State and Notify Channel ---
                    self.state.is_banned = True
                    # VC status update on ban removed by user request.
                    try:
                        chat_channel = self.bot.get_channel(
                            self.config.CHAT_CHANNEL_ID
                        )
                        if chat_channel:
                            message = f"@here The Streaming VC Bot just got banned on Omegle - Wait for Host OR use this URL in your browser to pay for an unban - Afterwards, just !skip and it should be unbanned!\n{current_url}"
                            ban_msg = await chat_channel.send(message)
                            self.state.ban_message_id = ban_msg.id
                            logger.info(
                                f"Sent ban notification (ID: {ban_msg.id}) to channel ID {self.config.CHAT_CHANNEL_ID}."
                            )
                    except Exception as e:
                        logger.error(f"Failed to send ban notification: {e}")

            # --- UNBAN DETECTION ---
            was_unbanned = False
            async with self.state.moderation_lock:
                # If we were banned but are now back on the main video page
                if (
                    self.config.OMEGLE_VIDEO_URL in current_url
                    and self.state.is_banned
                ):
                    logger.info(
                        "Proactive check detected the main video page. Attempting to announce unban."
                    )
                    try:
                        chat_channel = self.bot.get_channel(
                            self.config.CHAT_CHANNEL_ID
                        )
                        if chat_channel:
                            # Delete the old ban message if we have its ID
                            if self.state.ban_message_id:
                                try:
                                    old_ban_msg = await chat_channel.fetch_message(
                                        self.state.ban_message_id
                                    )
                                    await old_ban_msg.delete()
                                    logger.info(
                                        f"Successfully deleted old ban message (ID: {self.state.ban_message_id})."
                                    )
                                except discord.NotFound:
                                    logger.warning(
                                        "Tried to delete old ban message, but it was already gone."
                                    )
                                finally:
                                    self.state.ban_message_id = None

                            # Send new unban message
                            message = f"@here We are now unbanned on Omegle! Feel free to rejoin the <#{self.config.STREAMING_VC_ID}> VC!"
                            await chat_channel.send(message)
                            logger.info(
                                f"Sent proactive unbanned notification to channel ID {self.config.CHAT_CHANNEL_ID}."
                            )

                            # Reset state
                            self.state.is_banned = False
                            # VC status update on unban removed by user request.
                            self.state.relay_command_sent = False
                            was_unbanned = True
                            logger.info(
                                "Bot state updated to unbanned, relay command armed."
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to send proactive unbanned notification: {e}"
                        )

            # If we were just unbanned, execute the requested unban setup sequence
            if was_unbanned:
                logger.info(
                    "Unban detected. Executing setup sequence (Wait 5.3s -> Click Checks -> Relay -> Vol)..."
                )
                
                # 1. Wait 5.3 seconds
                await asyncio.sleep(5.3)
                
                # 2. Click Checkboxes
                await self.find_and_click_checkbox()
                
                # 3. Send /relay then Set Volume (via the modified _attempt_send_relay)
                await self._attempt_send_relay()

        except UnexpectedAlertPresentException:
            # Handle random browser alerts (e.g., "are you sure you want to leave?")
            try:
                def handle_alert():
                    alert = self.driver.switch_to.alert
                    alert_text = alert.text
                    alert.dismiss()  # Dismiss the alert
                    return alert_text

                alert_text = await asyncio.to_thread(handle_alert)
                logger.warning(
                    f"Handled and dismissed an unexpected browser alert. Text: '{alert_text}'"
                )
            except Exception as alert_e:
                logger.error(
                    f"Tried to handle an unexpected alert, but failed: {alert_e}"
                )
        except Exception as e:
            logger.error(f"Error during passive ban check: {e}", exc_info=True)