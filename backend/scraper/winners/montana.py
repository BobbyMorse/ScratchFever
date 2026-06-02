"""
Montana winners scraper.

montanalottery.com/winners-stories/ renders accordion-style winner cards
grouped by city. Each card looks like:

    <div class="ue-repeater-accordion-item">
      <div class="ue-repeater-accordion-item-header">
        <div class="ue-repeater-accordion-item-heading">Helena</div>
      </div>
      <div class="ue-repeater-accordion-item-content">
        <p><b>When:</b> 05/26/2026</p>
        <p><b>How Much:</b> $18,497</p>
        <p><b>Game:</b> Big Sky Bonus</p>
        <p><b>Purchased at:</b> Dave's Express in Helena</p>
      </div>
    </div>

"Purchased at" splits as "<Retailer> in <City>" so we get retailer name
plus a confirmation of city. The heading repeats the city, kept for safety.
"""
from __future__ import annotations
import datetime as dt
import logging
import re

from bs4 import BeautifulSoup

from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://montanalottery.com/winners-stories/"


class MontanaWinnersScraper(WinnersScraper):
    state_code = "MT"
    state_name = "Montana"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        resp = self.get(URL, timeout=60)
        soup = BeautifulSoup(resp.text, "lxml")

        out: list[dict] = []
        seen: set[str] = set()
        for card in soup.find_all("div", class_="ue-repeater-accordion-item"):
            norm = self._parse(card)
            if not norm:
                continue
            if norm["source_id"] in seen:
                continue
            seen.add(norm["source_id"])
            out.append(norm)
        logger.info("MT winners: %d cards parsed", len(out))
        return out

    def _parse(self, card) -> dict | None:
        heading = card.find(class_="ue-repeater-accordion-item-heading")
        city = heading.get_text(" ", strip=True) if heading else None

        fields = {}
        for p in card.find_all("p"):
            text = p.get_text(" ", strip=True)
            m = re.match(r"(When|How Much|Game|Purchased at)\s*:\s*(.+)$", text, re.IGNORECASE)
            if m:
                key = m.group(1).lower()
                fields[key] = m.group(2).strip()

        prize = _parse_money(fields.get("how much", ""))
        if prize is None or prize < self.min_prize:
            return None

        game_name = fields.get("game") or ""
        game_name = re.sub(r"\s+", " ", game_name).strip()
        if not game_name:
            return None
        if is_draw_game(self.state_code, game_name):
            return None

        claim_date = _parse_mdy(fields.get("when", ""))

        retailer_name = None
        retailer_city = city
        purchased_at = fields.get("purchased at", "")
        if purchased_at:
            split = re.split(r"\s+in\s+", purchased_at, maxsplit=1, flags=re.IGNORECASE)
            retailer_name = split[0].strip() or None
            if len(split) > 1:
                retailer_city = split[1].strip().rstrip(".") or retailer_city

        sid_parts = [
            (claim_date.isoformat() if claim_date else ""),
            f"{int(prize)}",
            game_name,
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
            "source_url": URL,
        }


def _parse_money(s: str) -> float | None:
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", s or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_mdy(s: str) -> dt.date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
