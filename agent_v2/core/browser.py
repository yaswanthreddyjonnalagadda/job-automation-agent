"""
core/browser.py - Async context manager initializing Playwright Chromium.
Applies dynamic viewports, user-agent masking, navigator.webdriver removal,
and human emulation for mouse movement curves and variable typing delays.
"""

from __future__ import annotations

import asyncio
import math
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
)

STANDARD_VIEWPORTS = (
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
)

STEALTH_INIT_SCRIPT = """
(() => {
    // Overwrite navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // Mock chrome runtime
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };

    // Mock plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
        configurable: true
    });

    // Mock languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true
    });
})();
"""


class HumanInputEmulator:
    """Human behavior emulator providing natural typing and curved mouse paths."""

    @staticmethod
    async def type_text(page: Page, selector: str, text: str, min_delay_ms: int = 50, max_delay_ms: int = 150) -> None:
        """Types text character by character with variable natural delays."""
        locator = page.locator(selector).first
        await locator.focus()
        for char in text:
            await page.keyboard.type(char)
            # Extra hesitation on space and punctuation
            if char in " .,-_@":
                delay = random.uniform(max_delay_ms, max_delay_ms * 1.8) / 1000.0
            else:
                delay = random.uniform(min_delay_ms, max_delay_ms) / 1000.0
            await asyncio.sleep(delay)

    @staticmethod
    async def move_and_click(page: Page, selector: str, steps: int = 12) -> None:
        """Move mouse in curved steps towards element coordinates and click naturally."""
        locator = page.locator(selector).first
        box = await locator.bounding_box()
        if not box:
            # Fallback direct click if bounding box unavailable
            await locator.click()
            return

        # Target a random coordinate within element's center area
        pad_x = box["width"] * 0.2
        pad_y = box["height"] * 0.2
        target_x = box["x"] + random.uniform(pad_x, box["width"] - pad_x)
        target_y = box["y"] + random.uniform(pad_y, box["height"] - pad_y)

        # Generate control points for Bezier curve simulation
        start_x, start_y = random.uniform(100, 300), random.uniform(100, 300)
        ctrl_x = (start_x + target_x) / 2 + random.uniform(-60, 60)
        ctrl_y = (start_y + target_y) / 2 + random.uniform(-60, 60)

        for step in range(1, steps + 1):
            t = step / steps
            # Quadratic Bezier formula
            x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * target_x
            y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * target_y
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.008, 0.022))

        # Mouse down, micro delay, mouse up
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.06, 0.12))
        await page.mouse.up()


class AsyncBrowserSession:
    """Async manager for Playwright Chromium session with stealth configurations."""

    def __init__(
        self,
        headless: bool = False,
        user_data_dir: Optional[Path] = None,
        viewport: Optional[dict[str, int]] = None,
        user_agent: Optional[str] = None,
    ):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.viewport = viewport or random.choice(STANDARD_VIEWPORTS)
        self.user_agent = user_agent or random.choice(USER_AGENTS)
        self.emulator = HumanInputEmulator()

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self) -> Page:
        """Launch browser and configure context."""
        self._playwright = await async_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
        ]

        if self.user_data_dir:
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                headless=self.headless,
                viewport=self.viewport,
                user_agent=self.user_agent,
                args=launch_args,
            )
            self.page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        else:
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=launch_args,
            )
            self._context = await self._browser.new_context(
                viewport=self.viewport,
                user_agent=self.user_agent,
            )
            self.page = await self._context.new_page()

        # Inject stealth scripts
        await self.page.add_init_script(STEALTH_INIT_SCRIPT)
        return self.page

    async def close(self) -> None:
        """Clean up browser resources."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()


@asynccontextmanager
async def launch_browser(
    headless: bool = False,
    user_data_dir: Optional[Path] = None,
) -> AsyncIterator[AsyncBrowserSession]:
    """Helper async context manager for clean with-block usage."""
    session = AsyncBrowserSession(headless=headless, user_data_dir=user_data_dir)
    try:
        await session.start()
        yield session
    finally:
        await session.close()

