"""
Playwright-based scraper base for JavaScript-rendered lottery sites.

Usage: subclass PlaywrightScraper instead of BaseScraper.
Call self.pw_soup(url) to get a BeautifulSoup from a JS-rendered page.
The browser is shared across all pw_soup calls within one scrape() invocation
and is automatically torn down when safe_scrape() exits.
"""
from __future__ import annotations
import logging
import os
from bs4 import BeautifulSoup
from backend.scraper.base import BaseScraper, HEADERS

# Ensure Playwright finds Chromium in the path used during the nixpacks build.
# Railway's startCommand env-var prefix doesn't always propagate to subprocesses.
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/app/.playwright"

logger = logging.getLogger(__name__)

_UA = HEADERS["User-Agent"]


class PlaywrightScraper(BaseScraper):
    """Base class for scrapers that need JavaScript rendering."""

    def __init__(self):
        super().__init__()
        self._pw = None
        self._browser = None

    # ── browser lifecycle ──────────────────────────────────────────────────────

    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            logger.debug("%s: browser started", self.state_code)

    def _close_browser(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None

    # ── public helpers ─────────────────────────────────────────────────────────

    def pw_soup(self, url: str, wait_for: str = "networkidle",
                selector: str = None, timeout: int = 30_000,
                extra_wait_ms: int = 0) -> BeautifulSoup:
        """Navigate to url with headless Chromium and return parsed HTML."""
        self._ensure_browser()
        ctx = self._browser.new_context(user_agent=_UA)
        page = ctx.new_page()
        try:
            page.goto(url, wait_until=wait_for, timeout=timeout)
            if selector:
                page.wait_for_selector(selector, timeout=timeout)
            if extra_wait_ms > 0:
                page.wait_for_timeout(extra_wait_ms)
            html = page.content()
        finally:
            page.close()
            ctx.close()
        return BeautifulSoup(html, "lxml")

    def pw_html(self, url: str, wait_for: str = "networkidle",
                selector: str = None, timeout: int = 30_000,
                extra_wait_ms: int = 0) -> str:
        """Like pw_soup but returns raw HTML string."""
        self._ensure_browser()
        ctx = self._browser.new_context(user_agent=_UA)
        page = ctx.new_page()
        try:
            page.goto(url, wait_until=wait_for, timeout=timeout)
            if selector:
                page.wait_for_selector(selector, timeout=timeout)
            if extra_wait_ms > 0:
                page.wait_for_timeout(extra_wait_ms)
            return page.content()
        finally:
            page.close()
            ctx.close()

    # ── safe_scrape override: run in a thread (sync_playwright needs no event
    # loop in its thread) and shut down browser when done ─────────────────────

    def safe_scrape(self) -> tuple[list[dict], str | None]:
        import concurrent.futures

        def _run():
            try:
                games = self.scrape()
                logger.info("%s: scraped %d games", self.state_code, len(games))
                return games, None
            except Exception as exc:
                logger.error("%s: scrape failed: %s", self.state_code, exc, exc_info=True)
                return [], str(exc)
            finally:
                self._close_browser()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_run).result()
