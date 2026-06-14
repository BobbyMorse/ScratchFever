"""
Tennessee Lottery scratch-off scraper.
tnlottery.com returns 403 Forbidden for all automated requests.
Disabled until an alternative data source or bypass is found.

Second-chance: TN's Play It Again page (tnvipsuite.com/PlayItAgain/Games)
is officially public but our scrape egress is also blocked. Moot while
the main TN scraper is disabled. has_second_chance stays FALSE.
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
