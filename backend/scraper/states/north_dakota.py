"""
North Dakota Lottery scratch-off scraper.
lottery.nd.gov only shows draw games in server-rendered HTML.
Scratch-off data is not accessible without JavaScript. Disabled.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class NorthDakotaScraper(BaseScraper):
    state_code = "ND"
    state_name = "North Dakota"
    base_url = "https://lottery.nd.gov"
    disabled = True

    def scrape(self) -> list[dict]:
        logger.info("ND: disabled (scratch-offs not accessible in static HTML)")
        return []
