"""
Connecticut winners scraper.

CT lottery publishes the most-recent ~17 winners as a single HTML table on
  https://www.ctlottery.org/winners
Pagination is JS-driven (no clean POST endpoint we can hit headlessly), so we
just take the visible page each hour; new wins accumulate over time.

Each row: Date, Winner+homeCity, Retailer name+city, Game, Prize.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.ctlottery.org/winners"

ROW_RE = re.compile(
    r'<tr>\s*'
    r'<td>\s*<time datetime="([^"]+)">[^<]+</time></td>\s*'
    r'<td>([^<]*)<br\s*/?>\s*([^<]+)</td>\s*'
    r'<td>(?:<a[^>]*>)?([^<]+)(?:</a>)?<br\s*/?>\s*([^<]+)</td>\s*'
    r'<td>([^<]+)</td>\s*'
    r'<td>\$([\d,.]+)</td>',
    re.IGNORECASE,
)


class ConnecticutWinnersScraper(WinnersScraper):
    state_code = "CT"
    state_name = "Connecticut"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()
        for m in ROW_RE.finditer(resp.text):
            date_iso, winner_name, winner_loc, retailer, ret_city, game, prize_raw = m.groups()
            try:
                prize = float(prize_raw.replace(",", ""))
            except ValueError:
                continue
            if prize < self.min_prize:
                continue
            game = game.strip()
            if not game or is_draw_game(self.state_code, game):
                continue
            try:
                claim_date = dt.date.fromisoformat(date_iso[:10])
            except ValueError:
                claim_date = None
            if claim_date and claim_date < cutoff:
                continue
            retailer = retailer.strip() or None
            ret_city = ret_city.strip() or None
            if retailer and retailer.lower() in {"online", "ctlottery", "ilottery"}:
                continue
            sid_parts = [date_iso[:10], retailer or "", ret_city or "", game, f"{int(prize)}"]
            source_id = "|".join(sid_parts)
            if source_id in seen:
                continue
            seen.add(source_id)
            out.append({
                "source_id": source_id,
                "source_game_id": None,
                "source_game_name": game,
                "prize_amount": prize,
                "claim_date": claim_date,
                "retailer_name": retailer,
                "retailer_city": ret_city,
                "retailer_address": None,
                "retailer_zip": None,
                "winner_city": None,
                "retailer_lat": None,
                "retailer_lng": None,
                "source_url": "https://www.ctlottery.org/winners",
            })
        return out
