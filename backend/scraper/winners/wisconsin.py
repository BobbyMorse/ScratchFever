"""
Wisconsin winners scraper.

WI Lottery's "All Winners" page is a Drupal infinite-scroll listing at
  https://wilottery.com/winners/all-winners?page=N
Each card has game-name, prize-amount, winner-name, retailer name + city + claim date.

This is a clean Tier-1 feed — retailer info is structured. We paginate from page
0 until we hit either an empty page or a page where every win is older than the
cutoff.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://wilottery.com/winners/all-winners"

CARD_RE = re.compile(
    r'<div class="vw-big-winners5">[\s\S]*?'
    r'<div class="game-name">\s*([^<]+?)\s*</div>[\s\S]*?'
    r'<div class="prize-amount">\s*\$?([\d,.]+)\s*</div>[\s\S]*?'
    r'<div class="winner-name">\s*([^<]*?)\s*</div>[\s\S]*?'
    r'<div class="date-loc">\s*'
    r'<div>\s*([^<]*?)\s*</div>\s*'
    r'<div>\s*([^<]*?)\s*</div>\s*'
    r'<div>\s*([^<]*?)\s*</div>',
)


class WisconsinWinnersScraper(WinnersScraper):
    state_code = "WI"
    state_name = "Wisconsin"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        out: list[dict] = []
        seen: set[str] = set()
        page = 0
        # Safety: WI publishes ~600 pages; cap well above that.
        while page < 2000:
            try:
                resp = self.get(URL, params={"page": page})
            except Exception as e:
                logger.warning("WI page %d failed: %s", page, e)
                break
            page_html = resp.text
            cards = CARD_RE.findall(page_html)
            if not cards:
                break
            stale_count = 0
            for game, prize_raw, winner, dateloc_html in cards:
                norm = self._normalize(game, prize_raw, winner, dateloc_html)
                if not norm:
                    continue
                if norm["claim_date"] and norm["claim_date"] < cutoff:
                    stale_count += 1
                    continue
                if norm["source_id"] in seen:
                    continue
                seen.add(norm["source_id"])
                out.append(norm)
            # If every card on this page was older than cutoff, stop paging back.
            if stale_count == len(cards) and stale_count > 0:
                break
            page += 1
        return out

    def _normalize(self, game_raw: str, prize_raw: str, winner: str,
                   dateloc_html: str) -> dict | None:
        try:
            prize = float(prize_raw.replace(",", ""))
        except ValueError:
            return None
        if prize < self.min_prize:
            return None

        game_name = (game_raw or "").strip()
        if not game_name:
            return None
        if is_draw_game(self.state_code, game_name):
            return None

        # date-loc contains three <div>s: retailer, city, date (MM/DD/YYYY).
        lines = LINE_RE.findall(dateloc_html)
        retailer = lines[0].strip() if len(lines) >= 1 else None
        city = lines[1].strip() if len(lines) >= 2 else None
        date_str = lines[2].strip() if len(lines) >= 3 else None

        claim_date = None
        if date_str:
            try:
                claim_date = dt.datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError:
                pass

        # Skip online/digital pseudo-retailers (no physical store).
        if retailer and retailer.lower() in {"online", "ilottery", "internet", "wilottery"}:
            return None

        sid_parts = [
            date_str or "",
            game_name,
            f"{int(prize)}",
            (winner or "").strip(),
            retailer or "",
            city or "",
        ]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": retailer,
            "retailer_city": city,
            "retailer_address": None,
            "retailer_zip": None,
            "winner_city": None,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": "https://wilottery.com/winners/all-winners",
        }
