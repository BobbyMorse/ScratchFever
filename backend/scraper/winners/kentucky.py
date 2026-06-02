"""
Kentucky winners scraper.

KY Lottery publishes a "Have You Heard?" feed at:
  https://www.kylottery.com/apps/winners/index.html
Each <article class="klc-home-heard-block"> contains:
  <h3>M.D.YY</h3>
  <p><strong>$AMOUNT GAME_NAME Scratch-off Winner!</strong></p>
  <p>Ticket sold at RETAILER in CITY, KY</p>

One article can list multiple winners under the same date. The page only
shows ~10 most recent articles (no archive/pagination), but hourly scrape
accumulates wins over time. Filtering on the "Scratch-off Winner!" suffix
in the <strong> tag cleanly excludes draw and Fast Play wins.
"""
from __future__ import annotations
import datetime as dt
import logging
import re

from backend.scraper.winners.base import WinnersScraper

logger = logging.getLogger(__name__)

URL = "https://www.kylottery.com/apps/winners/index.html"

ARTICLE_RE = re.compile(
    r'<article class="klc-grid-col-md-4 klc-grid-col-sm-6 klc-grid-col-xs-12 klc-home-heard-block">(.*?)</article>',
    re.DOTALL,
)
DATE_RE = re.compile(r'<h3>\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*</h3>')
# Every winner is "$AMOUNT NAME Winner!" — we capture all (scratch, draw, fast
# play) so retailer pairing stays positional, then filter on "Scratch-off"
# in the name to keep only scratch-off wins.
STRONG_RE = re.compile(
    r'<strong>\s*\$([\d,]+(?:\.\d+)?)\s+(.+?)\s+Winner!?\s*</strong>',
    re.IGNORECASE,
)
SCRATCH_RE = re.compile(r'\bScratch-off\b', re.IGNORECASE)
# "Ticket sold at NAME in CITY, KY" (real data also has the typo "Ticket old at")
TICKET_RE = re.compile(
    r'<p>\s*Ticket\s+(?:sold|old)\s+at\s+(.+?)\s+in\s+(.+?),\s*KY\s*</p>',
    re.IGNORECASE,
)


def _decode_nbsp(s: str) -> str:
    return s.replace("&nbsp;", " ").replace("\xa0", " ").strip()


class KentuckyWinnersScraper(WinnersScraper):
    state_code = "KY"
    state_name = "Kentucky"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        resp = self.get(URL)
        html = resp.text
        out: list[dict] = []
        seen: set[str] = set()

        for art_m in ARTICLE_RE.finditer(html):
            # &nbsp; isn't matched by \s, so normalize it (and decoded \xa0) to
            # plain spaces before parsing the block's <strong> / <p> elements.
            block = art_m.group(1).replace("&nbsp;", " ").replace("\xa0", " ")
            dm = DATE_RE.search(block)
            if not dm:
                continue
            month, day, year = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
            if year < 100:
                year += 2000
            try:
                claim_date = dt.date(year, month, day)
            except ValueError:
                continue
            if claim_date < cutoff:
                continue

            # Pair each <strong> with the next <p>Ticket sold at...</p>.
            # Include non-scratch wins in the iteration so retailer lines stay
            # aligned, then filter to scratch-off only at the bottom.
            strongs = list(STRONG_RE.finditer(block))
            tickets = list(TICKET_RE.finditer(block))
            ti = 0
            for sm in strongs:
                full_name = _decode_nbsp(sm.group(2))
                retailer = None
                city = None
                while ti < len(tickets) and tickets[ti].start() < sm.end():
                    ti += 1
                if ti < len(tickets):
                    retailer = _decode_nbsp(tickets[ti].group(1)) or None
                    city = _decode_nbsp(tickets[ti].group(2)).title() or None
                    ti += 1

                if not SCRATCH_RE.search(full_name):
                    continue
                # Strip the "Scratch-off" suffix from the captured game name.
                game = SCRATCH_RE.sub("", full_name).strip().rstrip("-").strip()
                if not game:
                    continue
                try:
                    prize = float(sm.group(1).replace(",", ""))
                except ValueError:
                    continue
                if prize < self.min_prize:
                    continue

                sid_parts = [
                    claim_date.isoformat(),
                    retailer or "",
                    city or "",
                    game,
                    f"{int(prize)}",
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
                    "retailer_city": city,
                    "retailer_address": None,
                    "retailer_zip": None,
                    "winner_city": None,
                    "retailer_lat": None,
                    "retailer_lng": None,
                    "source_url": URL,
                })
        return out
