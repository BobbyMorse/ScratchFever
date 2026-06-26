"""
Missouri winners scraper.

MO Lottery's monthly winners page:
  GET https://www.molottery.com/news/monthlywinners.do?method=Display
returns ONE month's $1K+ wins as a single HTML table with rows of:
  City (bold), Retailer, Address, Game, Prize.

The page is now Drupal-rendered and silently ignores the legacy `y`/`m` query
params — every request returns whichever month MO is currently publishing
(usually the most recently completed month). The earlier version of this
scraper iterated `_months_back(today, days)` and copied the same response
into each iterated month with a synthesized claim_date, which produced
fake "same retailer wins $100K on the 1st of every month for the past year"
records in reported_wins. Lesson logged in feedback memory.

We now make a single request, parse the month/year from the page header
("...sold in May 2026."), and stamp claim_date as month-end (or today, if
that month is still in progress).
"""
from __future__ import annotations
import calendar
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game


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

MONTH_RE = re.compile(r'sold in (\w+)\s+(\d{4})', re.IGNORECASE)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _month_end_or_today(year: int, month: int, today: dt.date) -> dt.date:
    last_day = calendar.monthrange(year, month)[1]
    candidate = dt.date(year, month, last_day)
    return min(candidate, today)


class MissouriWinnersScraper(WinnersScraper):
    state_code = "MO"
    state_name = "Missouri"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        # `days` is ignored: the MO page only ever publishes one month.
        # We accept the param for runner-API compatibility.
        resp = self.get(URL, params={"method": "Display"})
        text = resp.text

        m_meta = MONTH_RE.search(text)
        if not m_meta:
            logger.warning("MO: could not locate 'sold in <Month> <Year>' header — page format may have changed")
            return []
        month_name, year_str = m_meta.groups()
        month = _MONTHS.get(month_name.lower())
        try:
            year = int(year_str)
        except ValueError:
            year = None
        if not month or not year:
            logger.warning("MO: unparseable month/year header (%s %s)", month_name, year_str)
            return []

        today = dt.date.today()
        claim_date = _month_end_or_today(year, month, today)

        out: list[dict] = []
        seen: set[str] = set()
        for m in ROW_RE.finditer(text):
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
                "source_url": URL,
            })
        return out
