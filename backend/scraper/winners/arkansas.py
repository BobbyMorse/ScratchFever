"""
Arkansas winners scraper.

AR Lottery exposes a single-page winners table at:
  https://www.myarkansaslottery.com/winners
~72 most-recent rows, each with Name, City, Amount, Game, Date Claimed via
`data-cell-title`. No retailer info — winner home city only.

The page accepts a `?page=N` query param, but the param is silently ignored —
every page returns the same 72 rows. (Verified 2026-06-26.) The old paginated
loop iterated to the safety cap of 2000 pages × ~0.4s each and tripped the
600s scrape timeout every cycle, leaving AR stuck. We just fetch once.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.myarkansaslottery.com/winners"

ROW_RE = re.compile(
    r'<tr>\s*'
    r'<td data-cell-title="Name:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="City:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="Amount:\s*">\s*\$?([\d,.]+)\s*</td>\s*'
    r'<td data-cell-title="Game:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="Date Claimed:\s*">\s*([^<]*?)\s*</td>',
    re.IGNORECASE,
)


def _parse_date(raw: str) -> dt.date | None:
    raw = (raw or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


class ArkansasWinnersScraper(WinnersScraper):
    state_code = "AR"
    state_name = "Arkansas"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        # AR has very few $10K+ scratch wins (most rows are sub-$10K). A 14d
        # default window almost always returns 0; floor at 120d so a single
        # qualifying win every 1-2 months still lands.
        lookback_days = max(days, 120)
        cutoff = dt.date.today() - dt.timedelta(days=lookback_days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()
        for m in ROW_RE.finditer(resp.text):
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
        name, city, amt_raw, game, date_raw = m.groups()
        try:
            prize = float(amt_raw.replace(",", ""))
        except ValueError:
            return None
        if prize < self.min_prize:
            return None
        game = game.strip()
        if not game or is_draw_game(self.state_code, game):
            return None
        city = city.strip().title() or None
        if not city:
            return None
        claim_date = _parse_date(date_raw)
        sid_parts = [
            claim_date.isoformat() if claim_date else "",
            name.strip(), city, game, f"{int(prize)}",
        ]
        source_id = "|".join(sid_parts)
        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": None,
            "retailer_city": None,
            "retailer_address": None,
            "retailer_zip": None,
            "winner_city": city,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": "https://www.myarkansaslottery.com/winners",
        }
