"""
Oregon Lottery scratch-off scraper.
oregonlottery.org loads prize/odds values via JavaScript — blank placeholders in static HTML.
Disabled until a JS-capable scraper (Playwright) is implemented.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class OregonScraper(BaseScraper):
    state_code = "OR"
    state_name = "Oregon"
    base_url = "https://www.oregonlottery.org"

    def scrape(self) -> list[dict]:
        logger.info("OR: disabled (JavaScript-rendered page values)")
        return []
