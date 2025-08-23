# omegle.py
# This file contains the OmegleHandler class, which is responsible for all browser
# automation tasks using Selenium. It manages the WebDriver lifecycle and provides
# methods to interact with the Omegle web page.

import asyncio
import os
from functools import wraps
from typing import Optional, Union

import discord
from discord.ext import commands
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, StaleElementReferenceException
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

    def __init__(self, bot_config: BotConfig):
        self.config = bot_config
        self.driver: Optional[webdriver.Edge] = None
        self._driver_initialized = False # A flag to track if the driver has been successfully initialized.
        self.state: Optional[BotState] = None
        self._init_lock = asyncio.Lock() # Lock to prevent race conditions during driver initialization.

    async def initialize(self) -> bool:
        """
        Initializes the Selenium Edge WebDriver with enhanced anti-detection measures.
        It now lets Selenium's built-in SeleniumManager handle the driver automatically.

        Returns:
            True if the driver was initialized successfully, False otherwise.
        """
        for attempt in range(DRIVER_INIT_RETRIES):
            try:
                if self.driver is not None:
                    await self.close()

                logger.info("Initializing Selenium with automatic driver management...")
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
                
                # --- NEW, SIMPLIFIED INITIALIZATION ---
                # This single line replaces all the old webdriver-manager and service code.
                # Selenium's own manager will find and use the correct driver.
                self.driver = await asyncio.to_thread(webdriver.Edge, options=options)
                
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
                await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)

                self._driver_initialized = True
                logger.info("Selenium driver initialized successfully using automatic management.")
                return True
            except WebDriverException as e:
                logger.error(f"Selenium initialization attempt {attempt + 1} failed: {e}")
                if "This version of Microsoft Edge Driver only supports" in str(e):
                    logger.critical("CRITICAL: WebDriver version mismatch. Please update Edge browser or check for driver issues.")
                if attempt < DRIVER_INIT_RETRIES - 1:
                    await asyncio.sleep(DRIVER_INIT_DELAY)
            except Exception as e:
                logger.error(f"An unexpected error occurred during Selenium initialization: {e}", exc_info=True)
                break
        
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
        Executes a "skip" action on the web page by injecting JavaScript to simulate
        keyboard events (e.g., pressing the Escape key twice).
        
        Returns:
            True if successful, False otherwise.
        """
        # Get the keys to press from the config.
        keys = getattr(config, "SKIP_COMMAND_KEY", None)

        # If the key is not set, or set to None/0, default to Escape, Escape.
        if not keys:
            keys = ["Escape", "Escape"]

        # Ensure keys are always in a list for the loop.
        if not isinstance(keys, list):
            keys = [keys]

        # Retry loop to handle potential `StaleElementReferenceException`, which can happen
        # if the page structure changes while the command is running.
        for attempt in range(3):
            try:
                # Loop through the configured keys and dispatch a keydown event for each.
                for i, key in enumerate(keys):
                    script = f"""
                    var evt = new KeyboardEvent('keydown', {{
                        bubbles: true, cancelable: true, key: '{key}', code: '{key}'
                    }});
                    document.dispatchEvent(evt);
                    """
                    # Execute the JavaScript in a separate thread.
                    await asyncio.to_thread(self.driver.execute_script, script)
                    logger.info(f"Selenium: Sent {key} key event to page.")
                    if i < len(keys) - 1:
                        await asyncio.sleep(1) # Wait between key presses if multiple are configured.
                return True  # Success, exit the function.
            except StaleElementReferenceException:
                logger.warning(f"StaleElementReferenceException on attempt {attempt + 1}. Retrying...")
                await asyncio.sleep(0.5)
                continue  # Retry the operation.
            except Exception as e:
                logger.error(f"Selenium custom skip failed: {e}")
                if ctx: await ctx.send("Failed to execute skip command in browser.")
                return False

        logger.error("Failed to execute custom skip after multiple retries due to stale elements.")
        if ctx: await ctx.send("Failed to execute skip command after multiple retries.")
        return False

    @require_healthy_driver
    async def refresh(self, ctx: Optional[Union[commands.Context, discord.Message, discord.Interaction]] = None) -> bool:
        """
        Refreshes the stream page by navigating the browser back to the configured video URL.
        This is often used to fix a disconnected or frozen stream.
        
        Returns:
            True if successful, False otherwise.
        """
        try:
            # `driver.get()` is a blocking call.
            await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)
            logger.info("Selenium: Navigated to OMEGLE_VIDEO_URL for refresh command.")
            return True
        except Exception as e:
            logger.error(f"Selenium refresh failed: {e}")
            if ctx:
                error_msg = "Failed to process refresh command in browser."
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done(): await ctx.followup.send(error_msg)
                    else: await ctx.response.send_message(error_msg)
                elif hasattr(ctx, 'send'):
                    await ctx.send(error_msg)
            return False