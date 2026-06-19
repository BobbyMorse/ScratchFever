"""
Florida winners scraper.

Florida only publishes a small curated "Featured Winners" gallery at
floridalottery.com/winning/recent-winners — typically ~9 entries, all big
money ($500K-$10M+). Backed by a public AEM JSON resource:

  GET https://floridalottery.com/content/flalottery-web/us/en/winning/recent-winners.winners.json

Per-winner fields:
  name           "Billy Pitman"
  location       "Jacksonville, FL"     (winner home city)
  amountWon      "$1,000,000"            (string with $/commas)
  game           "Gold Rush Multiplier"  (scratch) or "Florida Lotto" (draw — filtered)
  pressReleaseUrl press release URL — also contains the YYYY-MM win date

No retailer info. City → pgeocode centroid for map pins. Small dataset but
all wins are 6+ figures.
"""
from __future__ import annotations
import calendar
import datetime as dt
import logging
import re

from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

API_URL = "https://floridalottery.com/content/flalottery-web/us/en/winning/recent-winners.winners.json"
SOURCE_URL = "https://floridalottery.com/winning/recent-winners"


class FloridaWinnersScraper(WinnersScraper):
    state_code = "FL"
    state_name = "Florida"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        resp = self.get(API_URL, headers={"Accept": "application/json"})
        data = resp.json()
        entries = data.get("data") or []
        out: list[dict] = []
        for e in entries:
            norm = self._normalize(e)
            if norm:
                out.append(norm)
        return out

    def _normalize(self, e: dict) -> dict | None:
        prize = _parse_amount(e.get("amountWon"))
        if prize is None or prize < self.min_prize:
            return None

        game_name = (e.get("game") or "").strip()
        if not game_name:
            # FL sometimes leaves game blank; assume scratch-off based on the
            # press-release URL which contains "scratch-off" for the vast
            # majority of these big wins.
            url = (e.get("pressReleaseUrl") or "").lower()
            if "scratch-off" not in url:
                return None
            game_name = "Scratch-Offs"
        elif is_draw_game(self.state_code, game_name):
            return None

        winner_city = _parse_city(e.get("location"))
        claim_date = _parse_date_from_url(e.get("pressReleaseUrl"))
        press_url = (e.get("pressReleaseUrl") or "").strip() or None

        # Stable id from the press-release URL (unique per win).
        sid = press_url or "|".join([
            e.get("name") or "", winner_city or "", f"{int(prize)}", game_name,
        ])

        return {
            "source_id": sid,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": None,
            "retailer_address": None,
            "retailer_city": None,
            "retailer_zip": None,
            "winner_city": winner_city,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": press_url or SOURCE_URL,
        }


def _parse_amount(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_city(loc: str | None) -> str | None:
    """'Jacksonville, FL' → 'Jacksonville'."""
    if not loc:
        return None
    city = re.sub(r",\s*(FL|Florida)\s*$", "", loc.strip(), flags=re.IGNORECASE)
    return city.strip() or None


def _parse_date_from_url(url: str | None) -> dt.date | None:
    """Press URLs are slugged like '?id=2025-12-duval-county-man-wins...'.
    The slug only carries year + month, not day. Map to month-end (or today
    if the slug is the current month) so the dashboard's "most recent claim"
    metric reflects how fresh the data actually is — using day=1 made every
    just-published Jan win appear 30 days old and tripped the SOURCE QUIET
    flag on a healthy feed."""
    if not url:
        return None
    m = re.search(r"id=(\d{4})-(\d{2})-", url)
    if not m:
        return None
    try:
        year, month = int(m.group(1)), int(m.group(2))
        last_day = calendar.monthrange(year, month)[1]
        candidate = dt.date(year, month, last_day)
        today = dt.date.today()
        return min(candidate, today)
    except ValueError:
        return None
