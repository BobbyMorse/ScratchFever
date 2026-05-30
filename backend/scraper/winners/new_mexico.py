"""
New Mexico winners scraper.

NM Lottery's winners page lists $5K+ wins as structured cards at:
  https://www.nmlottery.com/winners/
Each card has winner name (h3), "of <City>, <ST>" (winner home),
$amount (.winnings), date (.winning-date), game name + retailer block
(retailer name, street address, city) in winner-information.

The "Thousandaires" page covers $1K-$5K range — we ignore it ($10K floor).
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.nmlottery.com/winners/"

CARD_RE = re.compile(
    r'<div class="winner-content">\s*'
    r'<h3>\s*([^<]+?)\s*<br\s*/?>\s*'
    r'<span class="of-city">\s*of\s+([^<]+?)\s*</span>\s*'
    r'</h3>'
    r'[\s\S]{0,1000}?<p class="winnings">\s*\$([\d,.]+)\s*</p>'
    r'[\s\S]{0,2000}?<p class="winning-date">\s*([^<]+?)\s*</p>'
    r'\s*<p class="winner-information">\s*<p>\s*<strong>([^<]+?)</strong></p>\s*'
    r'<p>\s*([^<]+?)<br\s*/?>\s*'
    r'([^<]+?)<br\s*/?>\s*'
    r'([^<]+?)\s*</p>',
    re.IGNORECASE,
)


class NewMexicoWinnersScraper(WinnersScraper):
    state_code = "NM"
    state_name = "New Mexico"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()
        for m in CARD_RE.finditer(resp.text):
            name, of_city, prize_raw, date_raw, game, retailer, address, ret_city = m.groups()
            try:
                prize = float(prize_raw.replace(",", ""))
            except ValueError:
                continue
            if prize < self.min_prize:
                continue
            game = game.strip()
            if not game or is_draw_game(self.state_code, game):
                continue
            claim_date = None
            for fmt in ("%B %d, %Y", "%b %d, %Y"):
                try:
                    claim_date = dt.datetime.strptime(date_raw.strip(), fmt).date()
                    break
                except ValueError:
                    pass
            if claim_date and claim_date < cutoff:
                continue
            retailer = retailer.strip() or None
            address = address.strip() or None
            ret_city = ret_city.strip().rstrip(".").title() or None
            sid_parts = [
                claim_date.isoformat() if claim_date else "",
                name.strip(), retailer or "", ret_city or "",
                game, f"{int(prize)}",
            ]
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
                "retailer_address": address,
                "retailer_zip": None,
                "winner_city": None,
                "retailer_lat": None,
                "retailer_lng": None,
                "source_url": "https://www.nmlottery.com/winners/",
            })
        return out
