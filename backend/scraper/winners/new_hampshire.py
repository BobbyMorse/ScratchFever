"""
New Hampshire winners scraper.

NH's winners page (nhlottery.com/winning/winners) lists 49k+ wins and is
backed by a public JSON API exposed by the page's React bundle:

  GET https://prod.game-data.gambytservices.com/v1/winners
      ?resultStart=0&resultEnd=100
      &sortField=win_amount_in_cents&sortDirection=DESC
  Headers: x-api-key + referer (https://www.nhlottery.com/)

The API key is the public widget key embedded in NH's site JS — same key
served to every browser visitor. No auth flow.

Sorting by win_amount_in_cents DESC lets us page from the top and stop the
moment we cross under the $10K floor, so we never have to fetch the long
tail (~48k sub-$10K wins).

Per-win fields used:
  id (UUID, stable)              → source_id
  winAmountInCents (int)         → prize_amount
  claimDate (ISO)                → claim_date
  gameType ("INSTANT" / "DRAW")  → scratch filter
  gameName, gameId               → game linkage
  city (winner home city)        → winner_city → pgeocode centroid
  retailer (always null for NH)
"""
from __future__ import annotations
import datetime as dt
import logging

from backend.scraper.winners.base import WinnersScraper

logger = logging.getLogger(__name__)

API_URL = "https://prod.game-data.gambytservices.com/v1/winners"
PUBLIC_API_KEY = "1c4c69db-274c-4f59-95c5-3211cd74e9d8"
SOURCE_URL = "https://www.nhlottery.com/winning/winners"

PAGE_SIZE = 100
MAX_PAGES = 50  # safety cap — at $10K floor we expect <1k results


class NewHampshireWinnersScraper(WinnersScraper):
    state_code = "NH"
    state_name = "New Hampshire"
    min_prize = 10000.0

    def __init__(self):
        super().__init__()
        self.session.headers.update({
            "x-api-key": PUBLIC_API_KEY,
            "referer": "https://www.nhlottery.com/",
            "origin": "https://www.nhlottery.com",
            "accept": "application/json",
        })

    def scrape(self, days: int = 14) -> list[dict]:
        # `days` is ignored — we sort by amount DESC and stop at the floor.
        out: list[dict] = []
        min_cents = int(self.min_prize * 100)

        for page_idx in range(MAX_PAGES):
            start = page_idx * PAGE_SIZE
            end = start + PAGE_SIZE
            params = {
                "resultStart": start,
                "resultEnd": end,
                "sortField": "win_amount_in_cents",
                "sortDirection": "DESC",
            }
            resp = self.get(API_URL, params=params)
            data = resp.json()
            results = data.get("results") or []
            if not results:
                break

            below_floor = False
            for w in results:
                cents = w.get("winAmountInCents")
                if cents is None:
                    continue
                if cents < min_cents:
                    below_floor = True
                    break
                norm = self._normalize(w)
                if norm:
                    out.append(norm)

            if below_floor:
                break
            # Also stop if we've consumed the whole feed
            total = data.get("totalResults") or 0
            if end >= total:
                break

        return out

    def _normalize(self, w: dict) -> dict | None:
        if (w.get("gameType") or "").upper() != "INSTANT":
            return None  # scratch-only

        cents = w.get("winAmountInCents")
        if cents is None:
            return None
        prize = cents / 100.0

        sid = w.get("id")
        if not sid:
            return None

        claim_date = _parse_iso_date(w.get("claimDate") or w.get("sortDate"))

        game_name = (w.get("gameName") or "").strip()
        game_id = w.get("gameId")
        city = (w.get("city") or "").strip()
        # Source returns uppercase: "MANCHESTER". Title-case for cleaner display
        # and consistent pgeocode lookups.
        winner_city = city.title() if city else None

        return {
            "source_id": sid,
            "source_game_id": game_id,
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
            "source_url": SOURCE_URL,
        }


def _parse_iso_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None
