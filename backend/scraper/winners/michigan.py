"""
Michigan winners scraper.

GraphQL endpoint discovered from the michiganlottery.com "Big Winners" page.
  POST https://www.michiganlottery.com/api
  Root query: bigWinners(queryInput:{...}){ count results{ ... } }

Per-win fields: retailerName, retailerCity, gameName, prizeAmount, playerName, date
History: ~1.4M records dating back to game launches; no `retailerAddress` exposed.
"""
from __future__ import annotations
import datetime as dt
import logging
from backend.scraper.winners.base import WinnersScraper

logger = logging.getLogger(__name__)

API_URL = "https://www.michiganlottery.com/api"
PAGE_SIZE = 500

QUERY = """
query BigWinners($min: Int!, $n: Int!, $i: Int!, $from: String, $to: String) {
  bigWinners(queryInput: {
    numberOfResults: $n,
    startIndex: $i,
    minimumPrizeAmount: $min,
    startDateString: $from,
    endDateString: $to
  }) {
    count
    results {
      retailerName
      retailerCity
      gameName
      prizeAmount
      playerName
      date
    }
  }
}
""".strip()


class MichiganWinnersScraper(WinnersScraper):
    state_code = "MI"
    state_name = "Michigan"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        today = dt.date.today()
        date_from = (today - dt.timedelta(days=days)).isoformat()
        date_to = today.isoformat()

        out: list[dict] = []
        seen_ids: set[str] = set()
        start = 0
        total = None
        while True:
            payload = {
                "query": QUERY,
                "variables": {
                    "min": int(self.min_prize),
                    "n": PAGE_SIZE,
                    "i": start,
                    "from": date_from,
                    "to": date_to,
                },
            }
            resp = self.post(API_URL, json=payload, headers={
                "Content-Type": "application/json",
                "Referer": "https://www.michiganlottery.com/resources/big-winners",
                "Origin": "https://www.michiganlottery.com",
            })
            data = resp.json()
            bw = (data.get("data") or {}).get("bigWinners") or {}
            page = bw.get("results") or []
            if total is None:
                total = bw.get("count") or 0
            if not page:
                break
            for w in page:
                norm = self._normalize(w)
                if norm and norm["source_id"] not in seen_ids:
                    seen_ids.add(norm["source_id"])
                    out.append(norm)
            start += PAGE_SIZE
            # Safety cap: MI exposes ~1.4M records total; 500K covers any sane backfill.
            if start >= total or start >= 500_000:
                break
        return out

    def _normalize(self, w: dict) -> dict | None:
        prize_raw = w.get("prizeAmount")
        if prize_raw in (None, ""):
            return None
        try:
            prize = float(prize_raw)
        except (TypeError, ValueError):
            return None
        if prize < self.min_prize:
            return None

        date_str = w.get("date") or ""
        claim_date = None
        if date_str:
            try:
                claim_date = dt.date.fromisoformat(date_str[:10])
            except ValueError:
                pass

        game_name = (w.get("gameName") or "").strip()
        retailer = (w.get("retailerName") or "").strip() or None
        retailer_city = (w.get("retailerCity") or "").strip() or None
        winner = (w.get("playerName") or "").strip() or None

        # MI exposes no per-win id; build a stable composite.
        sid_parts = [
            (date_str or "")[:10],
            game_name,
            f"{int(prize)}",
            retailer or "",
            retailer_city or "",
            winner or "",
        ]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": None,
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
            "source_url": "https://www.michiganlottery.com/resources/big-winners",
        }
