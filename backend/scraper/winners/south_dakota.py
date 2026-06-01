"""
South Dakota winners scraper.

lottery.sd.gov/winners/ renders ~5-10 recent winner cards inline (small
state, low volume). All winners are "Anonymous" per SD policy, but each
card carries the *retailer* — including a street-level chain like
"Kessler Fuel, LLC" plus the city. That's enough for a retailer-pin
geocode via our existing state_retailers index.

Card markup is webpack/CSS-module class names that we match by suffix
substring (the random hashes change on every site rebuild):

    <div class="...winnerCardContent">
      <div class="...title">Anonymous</div>
      <div class="...game"><a title="Money Fever" href="...">Money Fever</a></div>
      <div class="...winnings">$40,000 Winner</div>
      <div class="...location">
        <div>Kessler Fuel, LLC</div>
        <div>Aberdeen</div>
      </div>
      <div class="...date">05.21.2026</div>
    </div>
"""
from __future__ import annotations
import datetime as dt
import logging
import re

from bs4 import BeautifulSoup

from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://lottery.sd.gov/winners/"
SOURCE_URL = URL


class SouthDakotaWinnersScraper(WinnersScraper):
    state_code = "SD"
    state_name = "South Dakota"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        resp = self.get(URL, timeout=60)
        soup = BeautifulSoup(resp.text, "lxml")

        # The webpack CSS module class names change across builds (hashed),
        # but the suffix pattern "__winnerCard" stays stable. Find every
        # container element whose class includes that token.
        cards = soup.find_all(class_=re.compile(r"__winnerCard(?!Content|Middle|Bottom)"))
        # Filter out nested wrappers — keep only outermost cards.
        cards = [c for c in cards if not any(p.get("class") and any(
            "__winnerCard" in cls for cls in p.get("class")) for p in c.parents)]

        out: list[dict] = []
        seen: set[str] = set()
        for card in cards:
            norm = self._parse(card)
            if not norm:
                continue
            if norm["source_id"] in seen:
                continue
            seen.add(norm["source_id"])
            out.append(norm)
        logger.info("SD winners: %d cards parsed", len(out))
        return out

    def _parse(self, card) -> dict | None:
        game_el = card.find(class_=re.compile(r"__game"))
        if not game_el:
            return None
        game_name = None
        link = game_el.find("a")
        if link:
            game_name = (link.get("title") or link.get_text(strip=True)).strip()
        else:
            game_name = game_el.get_text(strip=True)
        if not game_name:
            return None
        if is_draw_game(self.state_code, game_name):
            return None

        winnings_el = card.find(class_=re.compile(r"__winnings"))
        prize = _parse_money(winnings_el.get_text(" ", strip=True) if winnings_el else "")
        if prize is None or prize < self.min_prize:
            return None

        date_el = card.find(class_=re.compile(r"__date"))
        claim_date = _parse_date(date_el.get_text(strip=True) if date_el else "")

        retailer_name = None
        retailer_city = None
        location_el = card.find(class_=re.compile(r"__location"))
        if location_el:
            divs = [d.get_text(" ", strip=True) for d in location_el.find_all("div")]
            divs = [d for d in divs if d]
            if len(divs) >= 1:
                retailer_name = divs[0]
            if len(divs) >= 2:
                retailer_city = divs[1]

        # SD's listing never exposes a stable per-win ID. Build one from the
        # fields that uniquely identify a win (game + prize + date + retailer).
        sid_parts = [
            game_name,
            f"{int(prize)}",
            (claim_date.isoformat() if claim_date else ""),
            retailer_name or "",
            retailer_city or "",
        ]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": retailer_name,
            "retailer_address": None,
            "retailer_city": retailer_city,
            "retailer_zip": None,
            "winner_city": None,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": SOURCE_URL,
        }


def _parse_money(s: str) -> float | None:
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", s or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_date(s: str) -> dt.date | None:
    """SD uses 'MM.DD.YYYY' (period separators)."""
    if not s:
        return None
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
