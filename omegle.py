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
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from loguru import logger
import config
from tools import BotConfig, BotState
DRIVER_INIT_RETRIES = 2
DRIVER_INIT_DELAY = 5
SCREENSHOT_JPEG_QUALITY = 75
def require_healthy_driver(func):
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
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
        async def send_to_context(ctx, msg, ephemeral=False):
            if not ctx:
                return
            try:
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done():
                        await ctx.followup.send(msg, ephemeral=ephemeral)
                    else:
                        await ctx.response.send_message(msg, ephemeral=ephemeral)
                elif hasattr(ctx, 'send'):
                    await ctx.send(msg)
            except Exception as e:
                logger.error(f'Failed to send message to context: {e}')
        try:
            if not await self.is_healthy():
                async with self._init_lock:
                    if not await self.is_healthy():
                        logger.warning('Driver is unhealthy. Attempting to relaunch the browser...')
                        ctx = find_context()
                        await send_to_context(ctx, 'Browser connection lost. Attempting to relaunch...')
                        if not await self.initialize():
                            logger.critical('Failed to relaunch the browser after retries. Manual restart required.')
                            await send_to_context(ctx, 'Failed to relaunch the browser. Please restart the bot manually.', ephemeral=True)
                            return False
                        logger.info('Browser relaunched successfully.')
                        await send_to_context(ctx, 'Browser has been successfully relaunched.')
            return await func(self, *args, **kwargs)
        except (WebDriverException, StaleElementReferenceException) as e:
            ctx = find_context()
            if 'invalid session id' in str(e):
                logger.warning(f'Driver session invalid. Attempting to relaunch... (Error: {e.msg.splitlines()[0]})')
            else:
                logger.error(f'WebDriverException in {func.__name__}: {e}', exc_info=True)
            await send_to_context(ctx, 'Browser connection lost. Attempting to relaunch...')
            async with self._init_lock:
                if not await self.initialize():
                    logger.critical('Failed to relaunch the browser after retries. Manual restart required.')
                    await send_to_context(ctx, 'Failed to relaunch the browser. Please restart the bot manually.', ephemeral=True)
                    return False
                logger.info('Browser relaunched successfully.')
                await send_to_context(ctx, 'Browser has been successfully relaunched.')
                logger.info(f"Retrying command '{func.__name__}' after relaunch.")
                try:
                    return await func(self, *args, **kwargs)
                except Exception as retry_e:
                    logger.error(f"Command '{func.__name__}' failed even after relaunch: {retry_e}", exc_info=True)
                    await send_to_context(ctx, f'Command {func.__name__} failed after relaunch. Please try again.', ephemeral=True)
                    return False
    return wrapper
class OmegleHandler:
    async def _set_volume(self, volume_percentage: int=40) -> bool:
        logger.info(f'Attempting to set volume to {volume_percentage}%...')
        try:
            set_volume_script = f"\n            var slider = document.getElementById('vol-control');\n            if (slider) {{\n                slider.value = {volume_percentage};\n                var event = new Event('input', {{ bubbles: true }});\n                slider.dispatchEvent(event);\n                console.log('Volume set to {volume_percentage}%');\n                return true;\n            }} else {{\n                console.error('Volume slider #vol-control not found.');\n                return false;\n            }}\n            "
            volume_set = await asyncio.to_thread(self.driver.execute_script, set_volume_script)
            if volume_set:
                logger.info(f'Successfully executed script to set volume to {volume_percentage}%.')
                return True
            else:
                logger.warning('Volume slider element not found via script.')
                return False
        except Exception as e:
            logger.error(f'Error during volume automation: {e}')
            return False
    async def _attempt_send_relay(self) -> bool:
        async with self.state.moderation_lock:
            if not self.state or self.state.relay_command_sent:
                return True
            logger.info('Attempting to send /relay command and set volume...')
            await self._set_volume()
            try:
                chat_input_selector = 'textarea.messageInput'
                send_button_xpath = "//div[contains(@class, 'mainText') and text()='Send']"
                def send_relay_command():
                    try:
                        time.sleep(1.0)
                        chat_input = self.driver.find_element('css selector', chat_input_selector)
                        chat_input.send_keys('/relay')
                        time.sleep(0.5)
                        send_button = self.driver.find_element('xpath', send_button_xpath)
                        send_button.click()
                        return True
                    except Exception as e:
                        logger.warning(f'Could not find/interact with chat elements to send /relay. Will retry on next skip. Error: {e}')
                        return False
                relay_sent = await asyncio.to_thread(send_relay_command)
                if relay_sent:
                    self.state.relay_command_sent = True
                    logger.info('Successfully sent /relay command and updated state.')
                    return True
            except Exception as e:
                logger.error(f'An unexpected error occurred when trying to send /relay: {e}')
            logger.warning('Failed to send /relay command. Will retry on next skip.')
            return False
    def __init__(self, bot: commands.Bot, bot_config: BotConfig):
        self.bot = bot
        self.config = bot_config
        self.driver: Optional[webdriver.Edge] = None
        self._driver_initialized = False
        self.state: Optional[BotState] = None
        self._init_lock = asyncio.Lock()
    async def _is_streaming_vc_active(self) -> bool:
        try:
            guild = self.bot.get_guild(self.config.GUILD_ID)
            if not guild:
                logger.warning('Could not check VC status: Guild not found.')
                return False
            streaming_vc = guild.get_channel(self.config.STREAMING_VC_ID)
            if not streaming_vc or not isinstance(streaming_vc, discord.VoiceChannel):
                logger.warning('Could not check VC status: Streaming VC not found or invalid.')
                return False
            for member in streaming_vc.members:
                if member.bot or member.id in self.config.ALLOWED_USERS:
                    continue
                if member.voice and member.voice.self_video:
                    logger.info('Active user with camera on detected in streaming VC.')
                    return True
            logger.info('Streaming VC has no active users with camera on.')
            return False
        except Exception as e:
            logger.error(f'Error checking VC status: {e}')
            return False
    async def initialize(self) -> bool:
        for attempt in range(DRIVER_INIT_RETRIES):
            try:
                if self.driver is not None:
                    await self.close()
                options = webdriver.EdgeOptions()
                options.add_argument(f'user-data-dir={self.config.EDGE_USER_DATA_DIR}')
                options.add_argument('--ignore-certificate-errors')
                options.add_argument('--allow-running-insecure-content')
                options.add_argument('--log-level=3')
                options.add_argument('--disable-blink-features=AutomationControlled')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                options.add_argument('--disable-infobars')
                options.add_argument('--disable-popup-blocking')
                options.add_experimental_option('excludeSwitches', ['enable-automation', 'enable-logging'])
                options.add_experimental_option('useAutomationExtension', False)
                try:
                    logger.info('Initializing Selenium with automatic driver management...')
                    self.driver = await asyncio.to_thread(webdriver.Edge, options=options)
                    logger.info('Automatic driver management successful.')
                except WebDriverException as auto_e:
                    logger.warning(f'Automatic driver management failed: {auto_e}')
                    if self.config.EDGE_DRIVER_PATH and os.path.exists(self.config.EDGE_DRIVER_PATH):
                        logger.info(f'Attempting fallback with specified driver path: {self.config.EDGE_DRIVER_PATH}')
                        try:
                            service = Service(executable_path=self.config.EDGE_DRIVER_PATH)
                            self.driver = await asyncio.to_thread(webdriver.Edge, service=service, options=options)
                            logger.info('Fallback driver path successful.')
                        except Exception as fallback_e:
                            logger.error(f'Fallback driver path also failed: {fallback_e}')
                            raise fallback_e
                    else:
                        logger.warning('No fallback driver path specified or path is invalid. Retrying with automatic management.')
                        raise auto_e
                stealth_script = "\n                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});\n                window.navigator.chrome = {\n                    runtime: {},\n                };\n                Object.defineProperty(navigator, 'languages', {\n                    get: () => ['en-US', 'en'],\n                });\n                Object.defineProperty(navigator, 'plugins', {\n                    get: () => [1, 2, 3],\n                });\n                "
                await asyncio.to_thread(self.driver.execute_cdp_cmd, 'Page.addScriptToEvaluateOnNewDocument', {'source': stealth_script})
                if self.state and self.state.window_size and self.state.window_position:
                    try:
                        logger.info(f'Restoring window to size: {self.state.window_size} and position: {self.state.window_position}')
                        def set_geometry():
                            self.driver.set_window_size(self.state.window_size['width'], self.state.window_size['height'])
                            self.driver.set_window_position(self.state.window_position['x'], self.state.window_position['y'])
                        await asyncio.to_thread(set_geometry)
                    except Exception as geo_e:
                        logger.error(f'Failed to restore window geometry: {geo_e}')
                logger.info(f'Navigating to {self.config.OMEGLE_VIDEO_URL}...')
                await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)
                await asyncio.sleep(1.0)
                is_vc_active = await self._is_streaming_vc_active()
                if is_vc_active:
                    logger.info('VC is active. Attempting pre-relay skip during initialization...')
                    try:
                        keys = getattr(config, 'SKIP_COMMAND_KEY', None)
                        if not keys:
                            keys = ['Escape', 'Escape']
                        if not isinstance(keys, list):
                            keys = [keys]
                        for i, key in enumerate(keys):
                            script = f"\n                            var evt = new KeyboardEvent('keydown', {{\n                                bubbles: true, cancelable: true, key: '{key}', code: '{key}'\n                            }});\n                            document.dispatchEvent(evt);\n                            "
                            await asyncio.to_thread(self.driver.execute_script, script)
                            logger.info(f'Selenium (init): Sent {key} key event to page.')
                            if i < len(keys) - 1:
                                await asyncio.sleep(1)
                    except Exception as e:
                        logger.warning(f'Pre-relay skip during initialization failed: {e}')
                else:
                    logger.info('VC is not active. Starting Omegle session, then pausing...')
                    try:
                        await asyncio.sleep(1.5)
                        try:
                            def perform_checkbox_click_inline():
                                try:
                                    checkbox = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[type="checkbox"]')))
                                except Exception:
                                    logger.info('(Init) No checkbox found on the page (explicit wait timed out).')
                                    return False
                                if checkbox.is_selected():
                                    logger.info('(Init) Checkbox already selected, no action needed.')
                                    return False
                                logger.info('(Init) Checkbox found. Moving mouse to element and clicking...')
                                actions = ActionChains(self.driver)
                                actions.move_to_element(checkbox)
                                actions.pause(0.5)
                                actions.click(checkbox)
                                actions.perform()
                                return True
                            await asyncio.to_thread(perform_checkbox_click_inline)
                        except Exception as e:
                            logger.error(f'(Init) Inlined checkbox click failed: {e}')
                        await asyncio.sleep(1.0)
                        keys = getattr(config, 'SKIP_COMMAND_KEY', None)
                        if not keys:
                            keys = ['Escape', 'Escape']
                        if not isinstance(keys, list):
                            keys = [keys]
                        for i, key in enumerate(keys):
                            script = f"\n                            var evt = new KeyboardEvent('keydown', {{\n                                bubbles: true, cancelable: true, key: '{key}', code: '{key}'\n                            }});\n                            document.dispatchEvent(evt);\n                            "
                            await asyncio.to_thread(self.driver.execute_script, script)
                            logger.info(f'Selenium (init): Sent {key} key event to page.')
                            if i < len(keys) - 1:
                                await asyncio.sleep(1)
                        logger.info("Session started. Now refreshing to 'pause' (EMPTY_VC_PAUSE).")
                        try:
                            logger.info('(Init) Selenium: Attempting to refresh the page (F5).')
                            await asyncio.to_thread(self.driver.refresh)
                            if self.state:
                                async with self.state.moderation_lock:
                                    self.state.relay_command_sent = False
                                logger.info('(Init) Relay command armed to be sent on the next skip after refresh.')
                            logger.info('(Init) Selenium: Page refreshed successfully.')
                        except Exception as e:
                            logger.error(f'(Init) Inlined refresh failed: {e}')
                    except Exception as e:
                        logger.error(f"Selenium init 'start-then-pause' logic failed: {e}")
                if self.state:
                    self.state.relay_command_sent = False
                    if is_vc_active:
                        logger.info('VC active. Attempting to send /relay on startup...')
                        await asyncio.sleep(1.0)
                        await self._attempt_send_relay()
                    else:
                        logger.info('VC not active. Relay is armed for next user !skip.')
                else:
                    logger.warning('Bot state not attached to omegle_handler, cannot send /relay on startup.')
                self._driver_initialized = True
                logger.info('Selenium driver initialized successfully.')
                return True
            except Exception as e:
                logger.error(f'Selenium initialization attempt {attempt + 1} failed: {e}')
                if 'This version of Microsoft Edge Driver only supports' in str(e):
                    logger.critical('CRITICAL: WebDriver version mismatch. Please update Edge browser or check for driver issues.')
                if attempt < DRIVER_INIT_RETRIES - 1:
                    await asyncio.sleep(DRIVER_INIT_DELAY)
        logger.critical('Failed to initialize Selenium driver after retries.')
        self._driver_initialized = False
        return False
    async def is_healthy(self) -> bool:
        if not self._driver_initialized or self.driver is None:
            return False
        if self._init_lock.locked():
            return True
        try:
            await asyncio.to_thread(lambda: self.driver.current_url)
            return True
        except Exception:
            return False
    async def get_window_geometry(self) -> Optional[tuple[dict, dict]]:
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
            logger.error(f'Could not get window geometry: {e}')
            return None
    async def close(self) -> None:
        if self.driver is not None:
            try:
                await asyncio.to_thread(self.driver.quit)
                logger.info('Selenium driver closed.')
            except Exception as e:
                logger.error(f'Error closing Selenium driver: {e}')
            finally:
                self.driver = None
                self._driver_initialized = False
    @require_healthy_driver
    async def find_and_click_checkbox(self) -> bool:
        try:
            def perform_checkbox_click():
                try:
                    checkboxes = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'input[type="checkbox"]'))
                    )
                except Exception as wait_e:
                    logger.info(f'No checkboxes found on the page (explicit wait timed out).')
                    return False
                
                clicked_any = False
                if not checkboxes:
                    logger.info('No checkboxes found on the page.')
                    return False

                logger.info(f'Found {len(checkboxes)} checkbox(es). Clicking all that are not selected.')
                actions = ActionChains(self.driver)
                for checkbox in checkboxes:
                    try:
                        if not checkbox.is_selected():
                            logger.info('Checkbox found. Moving mouse to element and clicking...')
                            actions.move_to_element(checkbox).pause(0.5).click(checkbox).perform()
                            clicked_any = True
                        else:
                            logger.info('Checkbox already selected, no action needed.')
                    except StaleElementReferenceException:
                        logger.warning('Checkbox became stale while iterating. Skipping it.')
                        continue
                return clicked_any

            clicked = await asyncio.to_thread(perform_checkbox_click)
            if clicked:
                logger.info('Successfully clicked one or more checkboxes.')
            return True
        except NoSuchElementException:
            logger.info('No checkbox found on the page.')
            return False
        except Exception as e:
            logger.error(f'An error occurred while trying to click the checkbox: {e}', exc_info=True)
            return False
    @require_healthy_driver
    async def custom_skip(self, ctx: Optional[commands.Context]=None) -> bool:
        current_url = await asyncio.to_thread(lambda: self.driver.current_url)
        if self.config.OMEGLE_VIDEO_URL not in current_url:
            logger.warning(f'URL Mismatch: Not on video page (Currently at: {current_url}). Redirecting before skip.')
            if ctx:
                await ctx.send('Browser is on the wrong page. Redirecting to the stream now...', delete_after=10)
            await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)
            await asyncio.sleep(2.0)
        keys = getattr(config, 'SKIP_COMMAND_KEY', None)
        if not keys:
            keys = ['Escape', 'Escape']
        if not isinstance(keys, list):
            keys = [keys]
        skip_successful = False
        for attempt in range(3):
            try:
                for i, key in enumerate(keys):
                    script = f"\n                    var evt = new KeyboardEvent('keydown', {{\n                        bubbles: true, cancelable: true, key: '{key}', code: '{key}'\n                    }});\n                    document.dispatchEvent(evt);\n                    "
                    await asyncio.to_thread(self.driver.execute_script, script)
                    logger.info(f'Selenium: Sent {key} key event to page.')
                    if i < len(keys) - 1:
                        await asyncio.sleep(1)
                skip_successful = True
                break
            except StaleElementReferenceException:
                logger.warning(f'StaleElementReferenceException on skip attempt {attempt + 1}. Retrying...')
                await asyncio.sleep(0.5)
                continue
            except Exception as e:
                logger.error(f'Selenium custom skip failed: {e}')
                if ctx:
                    await ctx.send('Failed to execute skip command in browser.')
                skip_successful = False
                break
        if not skip_successful:
            logger.error('Failed to execute custom skip. Will still attempt volume/relay.')
        await self._attempt_send_relay()
        return skip_successful
    @require_healthy_driver
    async def refresh(self, ctx: Optional[Union[commands.Context, discord.Message, discord.Interaction]]=None) -> bool:
        current_url = await asyncio.to_thread(lambda: self.driver.current_url)
        if self.config.OMEGLE_VIDEO_URL not in current_url:
            logger.warning(f'URL Mismatch: Not on video page (Currently at: {current_url}). Redirecting before refresh.')
            if ctx:
                msg_content = 'Browser is on the wrong page. Redirecting to the stream now...'
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done():
                        await ctx.followup.send(msg_content, delete_after=10)
                    else:
                        await ctx.response.send_message(msg_content, delete_after=10)
                elif hasattr(ctx, 'send'):
                    await ctx.send(msg_content, delete_after=10)
            await asyncio.to_thread(self.driver.get, self.config.OMEGLE_VIDEO_URL)
            await asyncio.sleep(1.0)
        try:
            logger.info('Selenium: Attempting to refresh the page (F5).')
            await asyncio.to_thread(self.driver.refresh)
            if self.state:
                async with self.state.moderation_lock:
                    self.state.relay_command_sent = False
                logger.info('Relay command armed to be sent on the next skip after refresh.')
            logger.info('Selenium: Page refreshed successfully.')
            
            # --- START: v2.5 Feature Re-integration ---
            # Check if this was a user-initiated refresh (ctx will exist)
            if ctx is not None:
                logger.info('User-initiated refresh detected. Waiting 5.3s to click checkboxes...')
                await asyncio.sleep(5.3)
                await self.find_and_click_checkbox()
            else:
                logger.info('Automated refresh (e.g., auto-pause). Skipping checkbox click.')
            # --- END: v2.5 Feature Re-integration ---
                
            return True
        except Exception as e:
            logger.error(f'Selenium page refresh failed: {e}')
            if ctx:
                error_msg = 'Failed to refresh the browser page.'
                if isinstance(ctx, discord.Interaction):
                    if ctx.response.is_done():
                        await ctx.followup.send(error_msg)
                    else:
                        await ctx.response.send_message(error_msg)
                elif hasattr(ctx, 'send'):
                    await ctx.send(error_msg)
            return False
    @require_healthy_driver
    async def report_user(self, ctx: Optional[commands.Context]=None) -> bool:
        try:
            logger.info('Attempting to report user and take screenshot...')
            if self.config.SS_LOCATION:
                try:
                    os.makedirs(self.config.SS_LOCATION, exist_ok=True)
                    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                    sanitized_username = re.sub('[\\\\/*?:"<>|]', '', ctx.author.name)
                    filename = f'report-{timestamp}-{sanitized_username}.jpg'
                    filepath = os.path.join(self.config.SS_LOCATION, filename)
                    def capture_and_save_jpeg():
                        screenshot_data = self.driver.execute_cdp_cmd('Page.captureScreenshot', {'format': 'jpeg', 'quality': SCREENSHOT_JPEG_QUALITY})
                        img_bytes = base64.b64decode(screenshot_data['data'])
                        with open(filepath, 'wb') as f:
                            f.write(img_bytes)
                        return True
                    screenshot_saved = await asyncio.to_thread(capture_and_save_jpeg)
                    if screenshot_saved:
                        logger.info(f'Screenshot (JPEG, Q{SCREENSHOT_JPEG_QUALITY}) saved to: {filepath}')
                    else:
                        logger.error('Failed to save screenshot, method returned False.')
                except Exception as ss_e:
                    logger.error(f'Failed to take or send screenshot: {ss_e}', exc_info=True)
                    if ctx:
                        await ctx.send('⚠️ Failed to take screenshot, but proceeding with report.', delete_after=10)
            report_flag_xpath = "//img[@alt='Report' and contains(@class, 'reportButton')]"
            confirm_button_id = 'confirmBan'
            def click_elements():
                report_flag = self.driver.find_element('xpath', report_flag_xpath)
                report_flag.click()
                logger.info('Clicked the report flag icon.')
                time.sleep(1)
                confirm_button = self.driver.find_element('id', confirm_button_id)
                confirm_button.click()
                logger.info('Clicked the confirmation report button.')
            await asyncio.to_thread(click_elements)
            if ctx:
                await ctx.send('✅ User has been reported.', delete_after=10)
            return True
        except NoSuchElementException as e:
            logger.error(f'Failed to find report element: {e.msg}')
            if ctx:
                await ctx.send('❌ Failed to report user. Could not find report buttons on the page.', delete_after=10)
            return False
        except Exception as e:
            logger.error(f'Failed to report user: {e}', exc_info=True)
            if ctx:
                await ctx.send('❌ Failed to report user. See logs for details.', delete_after=10)
            return False
    @require_healthy_driver
    async def skip_from_hotkey(self) -> bool:
        logger.info('Global hotkey skip received. Executing custom_skip...')
        return await self.custom_skip(ctx=None)
    async def capture_and_store_screenshot(self) -> None:
        if not await self.is_healthy():
            return
        try:
            def capture_jpeg_bytes():
                screenshot_data = self.driver.execute_cdp_cmd('Page.captureScreenshot', {'format': 'jpeg', 'quality': SCREENSHOT_JPEG_QUALITY})
                return base64.b64decode(screenshot_data['data'])
            screenshot_bytes = await asyncio.to_thread(capture_jpeg_bytes)
            async with self.state.screenshot_lock:
                if not hasattr(self.state, 'ban_screenshots'):
                    self.state.ban_screenshots = []
                self.state.ban_screenshots.append((time.time(), screenshot_bytes))
                if len(self.state.ban_screenshots) > 3:
                    self.state.ban_screenshots.pop(0)
        except Exception as e:
            logger.error(f'Failed to capture and store screenshot for ban buffer: {e}')
    async def check_for_ban(self) -> None:
        if not await self.is_healthy():
            return
        try:
            current_url = await asyncio.to_thread(lambda: self.driver.current_url)
            async with self.state.moderation_lock:
                if '/ban/' in current_url and (not self.state.is_banned):
                    logger.warning(f'Proactive ban check detected a ban! URL: {current_url}.')
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
                            ban_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                            logger.bind(BAN_LOG=True).info(f'--- BAN DETECTED at {ban_time} ---')
                            human_members = [m for m in members_in_vc if not m.bot]
                            if human_members:
                                logger.bind(BAN_LOG=True).info(f'Users in streaming VC ({streaming_vc.name}):')
                                for member in human_members:
                                    logger.bind(BAN_LOG=True).info(f'  - UserID: {member.id:<20} | Username: {member.name:<32} | DisplayName: {member.display_name}')
                            else:
                                logger.bind(BAN_LOG=True).info('Streaming VC was empty of users at the time of the ban.')
                            logger.bind(BAN_LOG=True).info('--- END OF BAN REPORT ---')
                        else:
                            logger.error('Could not get guild to log users for ban report.')
                    except Exception as ban_log_e:
                        logger.error(f'Failed to write to ban.log: {ban_log_e}', exc_info=True)
                    if self.config.SS_LOCATION and hasattr(self.state, 'ban_screenshots'):
                        saved_filepaths = []
                        try:
                            async with self.state.screenshot_lock:
                                screenshots_to_save = self.state.ban_screenshots.copy()
                                self.state.ban_screenshots.clear()
                            if screenshots_to_save:
                                os.makedirs(self.config.SS_LOCATION, exist_ok=True)
                                ban_timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                                for i, (capture_time, ss_bytes) in enumerate(screenshots_to_save):
                                    filename = f'ban-{ban_timestamp}-{i + 1}.jpg'
                                    filepath = os.path.join(self.config.SS_LOCATION, filename)
                                    try:
                                        with open(filepath, 'wb') as f:
                                            f.write(ss_bytes)
                                        logger.info(f'Saved pre-ban screenshot to: {filepath}')
                                        saved_filepaths.append(filepath)
                                    except Exception as write_e:
                                        logger.error(f'Failed to write pre-ban screenshot {filename}: {write_e}')
                                logger.info(f'Successfully saved {len(screenshots_to_save)} pre-ban screenshots.')
                                stats_channel_id = self.config.AUTO_STATS_CHAN or self.config.CHAT_CHANNEL_ID
                                stats_channel = self.bot.get_channel(stats_channel_id)
                                if stats_channel and saved_filepaths:
                                    try:
                                        vc_mention = streaming_vc.mention if streaming_vc else f'<#{self.config.STREAMING_VC_ID}>'
                                        user_mentions = ' '.join([m.mention for m in human_members]) if human_members else 'No users were in the VC.'
                                        announcement_msg = f'@here The {vc_mention} VC was just banned on Omegle\nUsers in chat when banned: {user_mentions}'
                                        await stats_channel.send(announcement_msg, delete_after=120.0)
                                        files_to_send = [discord.File(fp) for fp in saved_filepaths]
                                        await stats_channel.send(files=files_to_send, delete_after=120.0)
                                        logger.info(f'Posted {len(saved_filepaths)} pre-ban screenshots to channel ID {stats_channel_id} (auto-delete 2m).')
                                    except discord.Forbidden:
                                        logger.error(f'Missing permissions to post pre-ban screenshots in channel ID {stats_channel_id}.')
                                    except Exception as post_e:
                                        logger.error(f'Failed to post pre-ban screenshots: {e}')
                                elif not stats_channel:
                                    logger.error(f'AUTO_STATS_CHAN (ID: {stats_channel_id}) not found for posting ban screenshots.')
                            else:
                                logger.warning('Ban detected, but screenshot buffer was empty.')
                        except Exception as ss_e:
                            logger.error(f'An error occurred while saving/posting pre-ban screenshots: {ss_e}')
                    self.state.is_banned = True
                    try:
                        chat_channel = self.bot.get_channel(self.config.CHAT_CHANNEL_ID)
                        if chat_channel:
                            message = f'@here The Streaming VC Bot just got banned on Omegle - Wait for Host OR use this URL in your browser to pay for an unban - Afterwards, just !skip and it should be unbanned!\n{current_url}'
                            ban_msg = await chat_channel.send(message)
                            self.state.ban_message_id = ban_msg.id
                            logger.info(f'Sent ban notification (ID: {ban_msg.id}) to channel ID {self.config.CHAT_CHANNEL_ID}.')
                    except Exception as e:
                        logger.error(f'Failed to send ban notification: {e}')
            was_unbanned = False
            async with self.state.moderation_lock:
                if self.config.OMEGLE_VIDEO_URL in current_url and self.state.is_banned:
                    logger.info('Proactive check detected the main video page. Attempting to announce unban.')
                    try:
                        chat_channel = self.bot.get_channel(self.config.CHAT_CHANNEL_ID)
                        if chat_channel:
                            if self.state.ban_message_id:
                                try:
                                    old_ban_msg = await chat_channel.fetch_message(self.state.ban_message_id)
                                    await old_ban_msg.delete()
                                    logger.info(f'Successfully deleted old ban message (ID: {self.state.ban_message_id}).')
                                except discord.NotFound:
                                    logger.warning('Tried to delete old ban message, but it was already gone.')
                                finally:
                                    self.state.ban_message_id = None
                            message = f'@here We are now unbanned on Omegle! Feel free to rejoin the <#{self.config.STREAMING_VC_ID}> VC!'
                            await chat_channel.send(message)
                            logger.info(f'Sent proactive unbanned notification to channel ID {self.config.CHAT_CHANNEL_ID}.')
                            self.state.is_banned = False
                            self.state.relay_command_sent = False
                            was_unbanned = True
                            logger.info('Bot state updated to unbanned, relay command armed.')
                    except Exception as e:
                        logger.error(f'Failed to send proactive unbanned notification: {e}')
            if was_unbanned:
                logger.info('Unban detected, attempting to send /relay and set volume...')
                await self._attempt_send_relay()
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
                logger.error(f'Tried to handle an unexpected alert, but failed: {alert_e}')
        except Exception as e:
            logger.error(f'Error during passive ban check: {e}')