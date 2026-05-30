"""
Massachusetts winners scraper.

API discovered from masslottery.com SPA JS bundle (fetchPageOfWinners):
  GET /api/v1/winners/query
  Params: start_index, count, date_from, date_to, games, prize_amounts, cities, sort
  Response: {"pageOfWinners":[...], "totalNumberOfWinners":N}

Per-win fields:
  date_of_win, prize_amount_usd, prize_amount_display,
  identifier (== games.game_id in our DB), name, retailer, retailer_location
"""
from __future__ import annotations
import datetime as dt
import logging
from backend.scraper.winners.base import WinnersScraper

logger = logging.getLogger(__name__)

API_URL = "https://www.masslottery.com/api/v1/winners/query"
PAGE_SIZE = 100
# MA accepts five fixed prize buckets — comma-joined gives us every $10K+ win.
PRIZE_BUCKETS_10K_PLUS = "10000-24999,25000-49999,50000-99999,100000-999999,1000000-"


class MassachusettsWinnersScraper(WinnersScraper):
    state_code = "MA"
    state_name = "Massachusetts"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        today = dt.date.today()
        date_from = (today - dt.timedelta(days=days)).isoformat()
        date_to = today.isoformat()

        out: list[dict] = []
        start = 0
        while True:
            params = {
                "start_index": start,
                "count": PAGE_SIZE,
                "date_from": date_from,
                "date_to": date_to,
            }
            resp = self.get(API_URL, params=params, headers={
                "Referer": "https://www.masslottery.com/tools/winners",
                "Origin": "https://www.masslottery.com",
            })
            data = resp.json()
            page = data.get("pageOfWinners") or []
            total = data.get("totalNumberOfWinners") or 0
            if not page:
                break
            for w in page:
                norm = self._normalize(w)
                if norm:
                    out.append(norm)
            start += PAGE_SIZE
            if start >= total or start >= 5000:  # safety cap
                break
        return out

    def _normalize(self, w: dict) -> dict | None:
        prize = w.get("prize_amount_usd")
        if prize is None:
            return None
        try:
            prize = float(prize)
        except (TypeError, ValueError):
            return None
        # Pre-filter to keep upsert payload small; base.safe_scrape filters again.
        if prize < self.min_prize:
            return None

        date_str = w.get("date_of_win")
        claim_date = None
        if date_str:
            try:
                claim_date = dt.date.fromisoformat(date_str[:10])
            except ValueError:
                pass

        identifier = (w.get("identifier") or "").strip() or None
        game_name = (w.get("name") or "").strip()
        retailer = (w.get("retailer") or "").strip() or None
        retailer_city = (w.get("retailer_location") or "").strip() or None

        # Stable source_id: date + identifier + prize + retailer + city.
        # MA does not expose a per-win id, but this combination is unique in practice.
        sid_parts = [
            date_str or "",
            identifier or game_name,
            f"{int(prize)}",
            retailer or "",
            retailer_city or "",
        ]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": identifier,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": retailer,
            "retailer_city": retailer_city,
            "retailer_address": None,
            "retailer_zip": None,
            "winner_city": None,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": "https://www.masslottery.com/tools/winners",
        }
