"""
Oklahoma Lottery scratch-off scraper.
lottery.ok.gov/scratchers is a JavaScript SPA — game data is not in server-rendered HTML.
Disabled until a JS-capable scraper (Playwright) is implemented.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class OklahomaScraper(BaseScraper):
    state_code = "OK"
    state_name = "Oklahoma"
    base_url = "https://www.lottery.ok.gov"

    def scrape(self) -> list[dict]:
        logger.info("OK: disabled (JavaScript SPA)")
        return []
