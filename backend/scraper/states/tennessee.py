"""
Tennessee Lottery scratch-off scraper.
tnlottery.com returns 403 Forbidden for all automated requests.
Disabled until an alternative data source or bypass is found.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class TennesseeScraper(BaseScraper):
    state_code = "TN"
    state_name = "Tennessee"
    base_url = "https://tnlottery.com"
    disabled = True

    def scrape(self) -> list[dict]:
        logger.info("TN: disabled (403 Forbidden)")
        return []
