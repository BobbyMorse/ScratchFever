"""
Missouri Lottery scratch-off scraper.
molottery.com/scratchers-list.do loads game data via JavaScript — server HTML has no game data.
Disabled until a JS-capable scraper (Playwright) is implemented.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class MissouriScraper(BaseScraper):
    state_code = "MO"
    state_name = "Missouri"
    base_url = "https://www.molottery.com"

    def scrape(self) -> list[dict]:
        logger.info("MO: disabled (JavaScript SPA)")
        return []
