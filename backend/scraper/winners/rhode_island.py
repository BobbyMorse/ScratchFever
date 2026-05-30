"""
Rhode Island winners scraper.

RI lottery publishes a single HTML table of recent instant-game winners at:
  https://www.rilot.com/content/interactive/ilottery/en/winners/winners-instant-games.html

The table has columns: Date, Game Number, Game Name, Amount, Retailer, City.
Returns ~370 rows back to 2020 (no pagination — all in one page). RI is a small
state, so this single page is the full feed.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = ("https://www.rilot.com/content/interactive/ilottery/en/winners/"
       "winners-instant-games.html")

ROW_RE = re.compile(r"<tr>([\s\S]*?)</tr>", re.IGNORECASE)
TD_RE = re.compile(r'<td title="([^"]*)">([^<]*)</td>')


class RhodeIslandWinnersScraper(WinnersScraper):
    state_code = "RI"
    state_name = "Rhode Island"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        today = dt.date.today()
        cutoff = today - dt.timedelta(days=days)
        resp = self.get(URL, headers={
            "Referer": "https://www.rilot.com/en-us/winners.html",
        })
        html = resp.text

        out: list[dict] = []
        seen: set[str] = set()
        for row_html in ROW_RE.findall(html):
            fields = dict(TD_RE.findall(row_html))
            if not fields:
                continue
            norm = self._normalize(fields)
            if not norm:
                continue
            if norm["claim_date"] and norm["claim_date"] < cutoff:
                continue
            if norm["source_id"] in seen:
                continue
            seen.add(norm["source_id"])
            out.append(norm)
        return out

    def _normalize(self, f: dict) -> dict | None:
        amt_raw = (f.get("Amount") or "").replace("$", "").replace(",", "").strip()
        if not amt_raw:
            return None
        try:
            prize = float(amt_raw)
        except ValueError:
            return None
        if prize < self.min_prize:
            return None

        game_name = (f.get("Game Name") or "").strip()
        if is_draw_game(self.state_code, game_name):
            return None

        date_str = (f.get("Date") or "").strip()
        claim_date = None
        if date_str:
            for fmt in ("%m/%d/%Y", "%-m/%-d/%Y"):
                try:
                    claim_date = dt.datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    pass

        retailer = (f.get("Retailer") or "").strip() or None
        # Skip online/digital-platform pseudo-retailers — no physical location.
        if retailer and retailer.lower() in {"ilottery", "online", "rilot.com"}:
            return None
        city = (f.get("City") or "").strip() or None
        if city and city.lower() in {"n/a", ""}:
            city = None

        game_id = (f.get("Game Number") or "").strip() or None
        if game_id and game_id.upper() == "N/A":
            game_id = None

        sid_parts = [
            date_str or "",
            game_id or game_name,
            f"{int(prize)}",
            retailer or "",
            city or "",
        ]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": game_id,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": retailer,
            "retailer_city": city,
            "retailer_address": None,
            "retailer_zip": None,
            "winner_city": None,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": "https://www.rilot.com/en-us/winners.html",
        }
