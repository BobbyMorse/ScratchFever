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

# Per-state draw-game blocklist. The big wins map is scratch-only; everything
# in this set is filtered at scrape time and during query (for historical rows).
# Matching is case-insensitive substring against source_game_name.
DRAW_GAME_PATTERNS: dict[str, tuple[str, ...]] = {
    "MA": (
        "keno", "mass cash", "powerball", "mega millions", "lucky for life",
        "numbers game", "megabucks", "all or nothing", "lucky for live",
    ),
    "MI": (
        "keno", "daily 3", "daily 4", "fantasy 5", "lotto 47", "mega millions",
        "powerball", "lucky for life", "poker lotto", "michigan cash drop",
        "fast cash", "pull tab",
    ),
    "RI": (
        "keno", "powerball", "mega millions", "lucky for life",
        "wild money", "the numbers", "millionaire for life",
    ),
    "TX": (
        "powerball", "mega millions", "lotto texas", "texas two step",
        "cash five", "pick 3", "all or nothing", "daily 4",
    ),
    "PA": (
        "powerball", "mega millions", "cash 5", "cash4life", "pick 2", "pick 3",
        "pick 4", "pick 5", "treasure hunt", "match 6", "millionaire raffle",
    ),
    "WI": (
        "powerball", "mega millions", "badger 5", "supercash", "pick 3",
        "pick 4", "all or nothing",
    ),
    "OK": (
        "powerball", "mega millions", "lotto america", "lucky for life",
        "pick 3", "cash 5",
    ),
    "VT": (
        "powerball", "mega millions", "lucky for life", "megabucks",
        "tri-state", "gimme 5", "pick 3", "pick 4",
    ),
    "MO": (
        "powerball", "mega millions", "show me cash", "lucky for life",
        "lotto", "pick 3", "pick 4", "club keno", "millionaire raffle",
    ),
    "AR": (
        "powerball", "mega millions", "lotto", "natural state jackpot",
        "cash 3", "cash 4", "lucky for life",
    ),
    "MN": (
        "powerball", "mega millions", "lotto america", "lucky for life",
        "gopher 5", "daily 3", "northstar cash",
    ),
    "LA": (
        "powerball", "mega millions", "lotto", "easy 5", "pick 3", "pick 4",
        "lucky for life",
    ),
    "IN": (
        "powerball", "mega millions", "hoosier lotto", "cash5", "cash 5",
        "lucky for life", "daily 3", "daily 4", "quick draw",
    ),
    "DE": (
        "powerball", "mega millions", "multi-win lotto", "play 3", "play 4",
        "lucky for life", "lotto america", "keno",
    ),
    "NM": (
        "powerball", "mega millions", "roadrunner cash", "pick 3", "pick 4",
        "lotto america",
    ),
    "MS": (
        "powerball", "mega millions", "match 5", "cash pop",
    ),
    "GA": (
        "powerball", "mega millions", "fantasy 5", "georgia 5", "cash 3",
        "cash 4", "jumbo bucks lotto", "cash pop",
    ),
    "CT": (
        "powerball", "mega millions", "lucky for life", "cash5", "cash 5",
        "play 3", "play 4", "keno", "lotto",
    ),
    "AZ": (
        "powerball", "mega millions", "fantasy 5", "the pick", "pick 3",
        "triple twist", "fastplay",
    ),
    "NH": (
        "powerball", "mega millions", "lucky for life", "megabucks",
        "tri-state", "gimme 5", "pick 3", "pick 4", "keno",
    ),
    "FL": (
        "powerball", "mega millions", "florida lotto", "cash4life",
        "lotto", "fantasy 5", "jackpot triple play", "pick 2", "pick 3",
        "pick 4", "pick 5", "cash pop", "cash4life",
    ),
    "NY": (
        "powerball", "mega millions", "lotto", "cash4life", "take 5",
        "pick 10", "numbers", "win 4", "quick draw",
    ),
    "IL": (
        "powerball", "mega millions", "lotto", "lucky day lotto",
        "pick 3", "pick 4", "lucky for life", "cash 5", "fast play",
    ),
    "IA": (
        "powerball", "mega millions", "lotto america", "lucky for life",
        "pick 3", "pick 4", "iowa lotto", "pick 5",
    ),
    "NE": (
        "powerball", "mega millions", "lotto america", "lucky for life",
        "pick 3", "pick 4", "pick 5", "nebraska pick", "millionaire for life",
        "2by2",
    ),
    "SD": (
        "powerball", "mega millions", "lotto america", "lucky for life",
        "dakota cash", "millionaire for life",
    ),
    "MT": (
        "powerball", "mega millions", "lucky for life", "montana cash",
        "big sky bonus", "shake a day", "pick 3", "pick 4",
    ),
    "NJ": (
        "powerball", "mega millions", "jersey cash 5", "pick-6", "pick 6",
        "cash4life", "fast play", "quick draw", "pick-3", "pick 3",
        "pick-4", "pick 4", "millionaire for life", "5 card cash",
    ),
    "KY": (
        "powerball", "mega millions", "lucky for life", "cash ball",
        "kentucky 5", "ky 5", "pick 3", "pick 4", "fast play", "keno",
    ),
}


def is_draw_game(state_code: str, game_name: str | None) -> bool:
    """True if game_name matches any draw-game pattern for the given state."""
    patterns = DRAW_GAME_PATTERNS.get(state_code, ())
    if not patterns:
        return False
    name = (game_name or "").lower()
    return any(p in name for p in patterns)

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

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 30)
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as e:
                last_exc = e
                resp = None
            if resp is not None and resp.status_code not in RETRY_STATUSES:
                resp.raise_for_status()
                return resp
            if attempt == MAX_RETRIES:
                if resp is not None:
                    resp.raise_for_status()
                raise last_exc  # type: ignore[misc]
            backoff = min(30.0, 1.5 ** attempt)
            logger.warning("%s %s attempt %d failed (%s) — sleeping %.1fs",
                           method, url, attempt,
                           resp.status_code if resp is not None else type(last_exc).__name__,
                           backoff)
            time.sleep(backoff)
        raise RuntimeError("unreachable")

    def get(self, url: str, **kwargs) -> requests.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self._request("POST", url, **kwargs)

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
