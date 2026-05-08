"""
West Virginia Lottery scratch-off scraper.
wvlottery.com is a JavaScript SPA — game data is not in server-rendered HTML.
Disabled until a JS-capable scraper (Playwright) is implemented.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class WestVirginiaScraper(BaseScraper):
    state_code = "WV"
    state_name = "West Virginia"
    base_url = "https://www.wvlottery.com"

    def scrape(self) -> list[dict]:
        logger.info("WV: disabled (JavaScript SPA)")
        return []
