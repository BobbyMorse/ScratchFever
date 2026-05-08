"""
Ohio Lottery scratch-off scraper.
DISABLED: ohiolottery.com was updated 2026-05-04 to a Vue.js SPA that requires
a Bearer token from authapi-solutions.ohiolottery.com. That auth endpoint blocks
all automated requests (times out from Python requests; 404 via browser-emulated
POST with correct headers). Old HTML URL patterns (/games/scratch-offs/$X-games/)
also return 404 after the update.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class OhioScraper(BaseScraper):
    state_code = "OH"
    state_name = "Ohio"
    base_url = "https://www.ohiolottery.com"

    def scrape(self) -> list[dict]:
        logger.info("OH: disabled (May 2026 site update broke API access)")
        return []
