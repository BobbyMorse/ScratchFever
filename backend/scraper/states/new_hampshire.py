"""
New Hampshire Lottery scratch-off scraper.
nhlottery.com is a JavaScript SPA — game listings don't appear in static HTML.
JSON API endpoints all return 404. Disabled.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class NewHampshireScraper(BaseScraper):
    state_code = "NH"
    state_name = "New Hampshire"
    base_url = "https://www.nhlottery.com"

    def scrape(self) -> list[dict]:
        logger.info("NH: disabled (JavaScript SPA, no accessible API)")
        return []
