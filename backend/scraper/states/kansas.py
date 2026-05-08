"""
Kansas Lottery scratch-off scraper.
kslottery.com redirects to playonkansas.com which serves eInstants (online-only
digital tickets) — not physical scratch-off tickets with odds tables.
Disabled: no physical scratch-off odds data available.
"""
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class KansasScraper(BaseScraper):
    state_code = "KS"
    state_name = "Kansas"
    base_url = "https://www.playonkansas.com"

    def scrape(self) -> list[dict]:
        logger.info("KS: disabled (eInstants only, no physical scratch-off odds)")
        return []
