"""
Delaware winners scraper.

DE Lottery's big-winners page exposes ~40 small HTML tables (one per game?),
each with rows of Date (M/D/YYYY), Location ("Store - City"), Amount.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.delottery.com/Winners/Big-Winners-Locations"

ROW_RE = re.compile(
    r'<td data-label="Date">\s*([^<]+?)\s*</td>\s*'
    r'<td data-label="Location">\s*([^<]+?)\s*</td>\s*'
    r'<td data-label="Amount">\s*\$([\d,.]+)\s*</td>',
    re.IGNORECASE,
)


class DelawareWinnersScraper(WinnersScraper):
    state_code = "DE"
    state_name = "Delaware"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()
        for m in ROW_RE.finditer(resp.text):
            date_raw, location, prize_raw = m.groups()
            try:
                prize = float(prize_raw.replace(",", ""))
            except ValueError:
                continue
            if prize < self.min_prize:
                continue
            try:
                claim_date = dt.datetime.strptime(date_raw.strip(), "%m/%d/%Y").date()
            except ValueError:
                claim_date = None
            if claim_date and claim_date < cutoff:
                continue
            # Location is like "Shore Stop #255 - Greentree" → split on " - "
            parts = location.split(" - ")
            retailer = parts[0].strip() if parts else None
            city = parts[1].strip() if len(parts) > 1 else None
            sid_parts = [
                claim_date.isoformat() if claim_date else "",
                retailer or "", city or "", f"{int(prize)}",
            ]
            source_id = "|".join(sid_parts)
            if source_id in seen:
                continue
            seen.add(source_id)
            out.append({
                "source_id": source_id,
                "source_game_id": None,
                "source_game_name": "Big Winner",  # DE doesn't expose game on this table
                "prize_amount": prize,
                "claim_date": claim_date,
                "retailer_name": retailer,
                "retailer_city": city,
                "retailer_address": None,
                "retailer_zip": None,
                "winner_city": None,
                "retailer_lat": None,
                "retailer_lng": None,
                "source_url": "https://www.delottery.com/Winners/Big-Winners-Locations",
            })
        return out
