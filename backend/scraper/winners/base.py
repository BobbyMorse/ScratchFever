"""
Common base for winners scrapers. Each subclass returns a list of dicts with:
  source_id          str   — unique within state (used for upsert dedupe)
  source_game_id     str?  — state's native game id, if available (used to link to games)
  source_game_name   str   — game name as published by source
  prize_amount       float
  claim_date         date  — when win was claimed/sold
  retailer_name      str?
  retailer_address   str?
  retailer_city      str?
  retailer_zip       str?
  winner_city        str?
  retailer_lat       float?  — fill if source publishes it; otherwise resolved at upsert
  retailer_lng       float?
  source_url         str?
"""
from __future__ import annotations
import logging
import time
from abc import ABC, abstractmethod
import requests

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}


class WinnersScraper(ABC):
    state_code: str = ""
    state_name: str = ""
    # Storage floor — we keep "large prize" wins long-term to power the
    # 3-year distribution map. Sub-$10K wins are skipped to keep table size sane.
    min_prize: float = 10000.0

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get(self, url: str, **kwargs) -> requests.Response:
        resp = self.session.get(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def post(self, url: str, **kwargs) -> requests.Response:
        resp = self.session.post(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    @abstractmethod
    def scrape(self, days: int = 14) -> list[dict]:
        """Return list of normalized win dicts."""

    def safe_scrape(self, days: int = 14) -> tuple[list[dict], str | None]:
        try:
            wins = self.scrape(days=days)
            wins = [w for w in wins if (w.get("prize_amount") or 0) >= self.min_prize]
            logger.info("%s winners: scraped %d wins (>=$%.0f)", self.state_code, len(wins), self.min_prize)
            return wins, None
        except Exception as e:
            logger.exception("%s winners scraper failed", self.state_code)
            return [], str(e)
