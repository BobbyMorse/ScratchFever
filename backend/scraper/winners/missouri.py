"""
Missouri winners scraper.

MO Lottery's monthly winners page:
  GET https://www.molottery.com/news/monthlywinners.do?method=Display&y=YYYY&m=M
returns one month's $10K+ wins as a single HTML table with rows of:
  City (bold), Retailer, Address, Game, Prize.

We iterate the last `days/30 + 1` months to cover the requested window.
"""
from __future__ import annotations
import calendar
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game


def _month_end_or_today(year: int, month: int, today: dt.date) -> dt.date:
    """Latest plausible claim date for a month-granularity win — keeps
    prior-month wins in the 30d display window long enough for the next
    month's data to publish."""
    last_day = calendar.monthrange(year, month)[1]
    candidate = dt.date(year, month, last_day)
    return min(candidate, today)

logger = logging.getLogger(__name__)

URL = "https://www.molottery.com/news/monthlywinners.do"

ROW_RE = re.compile(
    r'<tr>\s*'
    r'<td>\s*<b>([^<]*)</b>\s*</td>\s*'
    r'<td>([^<]*)</td>\s*'
    r'<td>([^<]*)</td>\s*'
    r'<td>([^<]*)</td>\s*'
    r'<td>\$([\d,.]+)</td>',
    re.IGNORECASE,
)


def _months_back(today: dt.date, days: int):
    cutoff = today - dt.timedelta(days=days)
    cur_y, cur_m = today.year, today.month
    while True:
        yield cur_y, cur_m
        first = dt.date(cur_y, cur_m, 1)
        if first < cutoff:
            return
        if cur_m == 1:
            cur_y -= 1
            cur_m = 12
        else:
            cur_m -= 1


class MissouriWinnersScraper(WinnersScraper):
    state_code = "MO"
    state_name = "Missouri"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        today = dt.date.today()
        out: list[dict] = []
        seen: set[str] = set()
        for year, month in _months_back(today, days):
            try:
                resp = self.get(URL, params={"method": "Display", "y": str(year), "m": str(month)})
            except Exception as e:
                logger.warning("MO %d-%d failed: %s", year, month, e)
                continue
            claim_date = dt.date(year, month, 1)
            for m in ROW_RE.finditer(resp.text):
                city, retailer, address, game, prize_raw = m.groups()
                try:
                    prize = float(prize_raw.replace(",", ""))
                except ValueError:
                    continue
                if prize < self.min_prize:
                    continue
                game = game.strip()
                if not game or is_draw_game(self.state_code, game):
                    continue
                city = city.strip() or None
                retailer = retailer.strip() or None
                address = address.strip() or None
                sid_parts = [f"{year:04d}-{month:02d}", retailer or "", city or "",
                             game, f"{int(prize)}"]
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
                    "retailer_city": city,
                    "retailer_address": address,
                    "retailer_zip": None,
                    "winner_city": None,
                    "retailer_lat": None,
                    "retailer_lng": None,
                    "source_url": "https://www.molottery.com/news/monthlywinners.do",
                })
        return out
