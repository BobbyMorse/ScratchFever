"""
Washington winners scraper.

WA Lottery exposes a single ~10MB HTML page at:
  https://walottery.com/Winners/Search.aspx
Each winner is its own `<table>` with rows for date+game, name+prize, and
LOCATION (store name + street address, city, WA). Multi-month coverage in one
fetch. We pull and filter to scratch only.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://walottery.com/Winners/Search.aspx"

# A single winner block looks like:
# <table>
#   <tr><td><strong>May 29, 2026</strong></td>
#       <td>... <p>$X GAME NAME</p> ...</td></tr>
#   ...
#   <tr><td><strong>NAME:</strong> JANE D.</td>
#       <td>$50,000</td></tr>
#   <tr><td colspan="2"><strong>LOCATION:</strong> STORE NAME<br>STREET, CITY, WA</td></tr>
# </table>
BLOCK_RE = re.compile(
    r'<tr>\s*<td><strong>([A-Z][a-z]+\s+\d+,\s*\d{4})</strong></td>\s*'
    r'<td>[\s\S]*?<p>([^<]+)</p>[\s\S]*?</td>\s*</tr>'
    r'[\s\S]*?<tr>\s*<td><strong>NAME:</strong>\s*([^<]+?)</td>\s*'
    r'<td>\$([\d,.]+)</td>\s*</tr>'
    r'\s*<tr>\s*<td[^>]*><strong>LOCATION:</strong>\s*([^<]+?)<br[^>]*>\s*([^<]+?)</td>',
    re.IGNORECASE,
)


class WashingtonWinnersScraper(WinnersScraper):
    state_code = "WA"
    state_name = "Washington"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()
        for m in BLOCK_RE.finditer(resp.text):
            date_raw, game, name, prize_raw, store, addr_city = m.groups()
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
                claim_date = dt.datetime.strptime(date_raw.strip(), "%B %d, %Y").date()
            except ValueError:
                claim_date = None
            if claim_date and claim_date < cutoff:
                continue

            store = store.strip() or None
            # addr_city is like "4010 A ST SE, AUBURN, WA" — split off city
            parts = [p.strip() for p in addr_city.split(",")]
            city = None
            address = None
            if len(parts) >= 3:
                address = parts[0]
                city = parts[1].title()
            elif len(parts) == 2:
                city = parts[0].title()
            sid_parts = [
                claim_date.isoformat() if claim_date else "",
                name.strip(), store or "", city or "",
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
                "retailer_name": store,
                "retailer_city": city,
                "retailer_address": address,
                "retailer_zip": None,
                "winner_city": None,
                "retailer_lat": None,
                "retailer_lng": None,
                "source_url": "https://walottery.com/Winners/Search.aspx",
            })
        return out
