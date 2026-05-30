"""
Minnesota winners scraper.

MN Lottery's winners page returns 12 cards per page with structured spans:
  https://www.mnlottery.com/winners/game?page=N
Card spans: winner-category (game), winner-info ("<store> in <city>, MN"),
winner-date ("Month D, YYYY"), winner-payout ($amount).
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


class MinnesotaWinnersScraper(WinnersScraper):
    state_code = "MN"
    state_name = "Minnesota"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        out: list[dict] = []
        seen: set[str] = set()
        page = 1
        while page < 1000:
            try:
                resp = self.get(URL, params={"page": page})
            except Exception as e:
                logger.warning("MN page %d failed: %s", page, e)
                break
            matches = list(CARD_RE.finditer(resp.text))
            if not matches:
                break
            stale = 0
            for m in matches:
                norm = self._normalize(m)
                if not norm:
                    continue
                if norm["claim_date"] and norm["claim_date"] < cutoff:
                    stale += 1
                    continue
                if norm["source_id"] in seen:
                    continue
                seen.add(norm["source_id"])
                out.append(norm)
            if stale == len(matches) and stale > 0:
                break
            page += 1
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
        claim_date = None
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                claim_date = dt.datetime.strptime(date_raw.strip(), fmt).date()
                break
            except ValueError:
                pass
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
