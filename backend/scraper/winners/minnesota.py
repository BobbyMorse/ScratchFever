"""
Minnesota winners scraper.

MN Lottery's winners page returns the 12 most-recent cards at:
  https://www.mnlottery.com/winners/game
Card spans: winner-category (game), winner-info ("<store> in <city>, MN"),
winner-date ("Month D, YYYY"), winner-payout ($amount).

The page accepts a `?page=N` query param, but the param is silently ignored
— every page returns the same 12 cards. (Verified 2026-06-26.) The old
paginated loop iterated to the 1000-page safety cap and tripped the 600s
scrape timeout every cycle, leaving MN stuck. We just fetch once.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.mnlottery.com/winners/game"

CARD_RE = re.compile(
    r'winner-category">\s*([^<]+?)\s*</span>[\s\S]{0,4000}?'
    r'winner-info">\s*([^<]+?)\s*</span>[\s\S]{0,1500}?'
    r'winner-date">\s*([^<]+?)\s*</span>[\s\S]{0,1500}?'
    r'winner-payout">\s*\$?([\d,.]+)\s*</span>',
    re.IGNORECASE,
)
INFO_RE = re.compile(r'^(?P<store>.+?)\s+in\s+(?P<city>.+?),\s*[A-Z]{2}$')


def _parse_date(raw: str) -> dt.date | None:
    raw = (raw or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


class MinnesotaWinnersScraper(WinnersScraper):
    state_code = "MN"
    state_name = "Minnesota"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()
        for m in CARD_RE.finditer(resp.text):
            norm = self._normalize(m)
            if not norm:
                continue
            if norm["claim_date"] and norm["claim_date"] < cutoff:
                continue
            if norm["source_id"] in seen:
                continue
            seen.add(norm["source_id"])
            out.append(norm)
        return out

    def _normalize(self, m) -> dict | None:
        game, info, date_raw, prize_raw = m.groups()
        try:
            prize = float(prize_raw.replace(",", ""))
        except ValueError:
            return None
        if prize < self.min_prize:
            return None
        game = game.strip()
        if not game or is_draw_game(self.state_code, game):
            return None
        im = INFO_RE.match(info.strip())
        retailer = im.group("store").strip() if im else None
        city = im.group("city").strip() if im else None
        claim_date = _parse_date(date_raw)
        sid_parts = [
            claim_date.isoformat() if claim_date else "",
            retailer or "", city or "", game, f"{int(prize)}",
        ]
        source_id = "|".join(sid_parts)
        return {
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
            "source_url": "https://www.mnlottery.com/winners/game",
        }
