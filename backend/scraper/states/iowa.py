"""
Iowa Lottery scratch-off scraper.
ialottery.com uses ASP.NET postback navigation — scratch games page
requires JavaScript execution. Disabled until Playwright is implemented.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class IowaScraper(BaseScraper):
    state_code = "IA"
    state_name = "Iowa"
    base_url = "https://ialottery.com"

    def scrape(self) -> list[dict]:
        logger.info("IA: disabled (ASP.NET postback navigation requires JavaScript)")
        return []
