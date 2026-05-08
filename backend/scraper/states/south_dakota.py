"""
South Dakota Lottery scratch-off scraper.
lottery.sd.gov/scratch-games/ is a JavaScript SPA — game data is not in server-rendered HTML.
Disabled until a JS-capable scraper (Playwright) is implemented.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class SouthDakotaScraper(BaseScraper):
    state_code = "SD"
    state_name = "South Dakota"
    base_url = "https://lottery.sd.gov"

    def scrape(self) -> list[dict]:
        logger.info("SD: disabled (JavaScript SPA)")
        return []
