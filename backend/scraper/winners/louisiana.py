"""
Louisiana winners scraper.

LA Lottery's winners page is a paginated WordPress archive:
  https://louisianalottery.com/winners/?paged=N
Each winner is its own `<article>` with: winner name (<h2>), datetime, Game,
Amount Won, Hometown (winner home city — not the retailer city), Retailer name.

We page until empty or all entries on a page are older than the cutoff.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://louisianalottery.com/winners/"

WINNER_RE = re.compile(
    r'<h2 class="winner-summary__title">\s*<a[^>]*>([^<]+)</a>\s*</h2>'
    r'[\s\S]{0,500}?<time datetime="([^"]+)"[^>]*>[^<]+</time>'
    r'[\s\S]{0,1000}?Game:\s*<a[^>]*>([^<]+)</a>'
    r'[\s\S]{0,1500}?Amount Won:\s*\$([\d,.]+)'
    r'[\s\S]{0,1500}?Hometown:\s*([^<]+?)\s*</span>'
    r'(?:[\s\S]{0,2500}?Retailer:\s*<a[^>]*>\s*([^<]+?)\s*</a>)?',
    re.IGNORECASE,
)


class LouisianaWinnersScraper(WinnersScraper):
    state_code = "LA"
    state_name = "Louisiana"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        out: list[dict] = []
        seen: set[str] = set()
        page = 1
        while page < 200:
            try:
                resp = self.get(URL, params={"paged": page})
            except Exception as e:
                logger.warning("LA page %d failed: %s", page, e)
                break
            matches = list(WINNER_RE.finditer(resp.text))
            if not matches:
                break
            stale = 0
            new_this_page = 0
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
                new_this_page += 1
            if stale == len(matches) and stale > 0:
                break
            if new_this_page == 0 and stale == 0:
                # No usable matches at all on this page — stop.
                break
            page += 1
        return out

    def _normalize(self, m) -> dict | None:
        name, date_iso, game, prize_raw, hometown, retailer = m.groups()
        try:
            prize = float(prize_raw.replace(",", ""))
        except ValueError:
            return None
        if prize < self.min_prize:
            return None
        game = game.strip()
        if not game or is_draw_game(self.state_code, game):
            return None
        try:
            claim_date = dt.date.fromisoformat(date_iso[:10])
        except ValueError:
            claim_date = None
        hometown = (hometown or "").strip().title() or None
        retailer = (retailer or "").strip() or None
        sid_parts = [
            claim_date.isoformat() if claim_date else "",
            name.strip(), hometown or "", retailer or "",
            game, f"{int(prize)}",
        ]
        source_id = "|".join(sid_parts)
        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": retailer,
            "retailer_city": None,
            "retailer_address": None,
            "retailer_zip": None,
            # LA's "Hometown" is the winner's, not the retailer's — store there.
            "winner_city": hometown,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": "https://louisianalottery.com/winners/",
        }
