"""
Washington Lottery scratch-off scraper.
walottery.com/Scratch/ uses href="#" placeholders for all game links — JavaScript SPA.
Disabled until a JS-capable scraper (Playwright) is implemented.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class WashingtonScraper(BaseScraper):
    state_code = "WA"
    state_name = "Washington"
    base_url = "https://www.walottery.com"

    def scrape(self) -> list[dict]:
        logger.info("WA: disabled (JavaScript SPA)")
        return []
