"""
Vermont winners scraper.

VT lottery publishes one big HTML table (~1.4MB) at:
  https://vtlottery.com/win/winners
Columns: Date (MM/DD/YYYY), Store Name, Town, State, Game, Prize Amount.
All winners on one page (no pagination), back several years.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://vtlottery.com/win/winners"

ROW_RE = re.compile(
    r'<tr class="visible">\s*'
    r'<td headers="view-field-win-date-table-column"[^>]*>\s*([^<]+?)\s*</td>\s*'
    r'<td headers="view-field-location-table-column"[^>]*>\s*([^<]*?)\s*</td>\s*'
    r'<td headers="view-field-location-town-table-column"[^>]*>\s*([^<]*?)\s*</td>\s*'
    r'<td headers="view-nothing-table-column"[^>]*>\s*([^<]*?)\s*</td>\s*'
    r'<td headers="view-field-game-name-table-column"[^>]*>\s*([^<]*?)\s*</td>\s*'
    r'<td headers="view-field-prize-amount-table-column"[^>]*>\s*\$([\d,.]+)\s*</td>',
    re.IGNORECASE,
)


class VermontWinnersScraper(WinnersScraper):
    state_code = "VT"
    state_name = "Vermont"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()
        for m in ROW_RE.finditer(resp.text):
            date_str, retailer, town, _state, game, prize_raw = m.groups()
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
                claim_date = dt.datetime.strptime(date_str.strip(), "%m/%d/%Y").date()
            except ValueError:
                claim_date = None
            if claim_date and claim_date < cutoff:
                continue
            retailer = retailer.strip() or None
            town = town.strip() or None
            sid_parts = [date_str.strip(), retailer or "", town or "", game, f"{int(prize)}"]
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
                "retailer_city": town,
                "retailer_address": None,
                "retailer_zip": None,
                "winner_city": None,
                "retailer_lat": None,
                "retailer_lng": None,
                "source_url": "https://vtlottery.com/win/winners",
            })
        return out
