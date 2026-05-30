"""
Oklahoma winners scraper.

OK Lottery exposes a paginated JSON endpoint:
  GET https://www.lottery.ok.gov/winners/get-winners/?page=N
Returns {TotalPages, Winners:[{Name, GameName, Amount, City, DateWon, Id}]}.

No retailer info — winner home city only. Stops paging once the oldest
result on a page is older than the cutoff.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.lottery.ok.gov/winners/get-winners/"
DOTNET_DATE_RE = re.compile(r"/Date\((\d+)\)/")


def _parse_dotnet_date(s: str) -> dt.date | None:
    if not s:
        return None
    m = DOTNET_DATE_RE.search(s)
    if not m:
        return None
    try:
        return dt.datetime.utcfromtimestamp(int(m.group(1)) / 1000.0).date()
    except (ValueError, OverflowError):
        return None


class OklahomaWinnersScraper(WinnersScraper):
    state_code = "OK"
    state_name = "Oklahoma"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        out: list[dict] = []
        seen: set[str] = set()
        page = 1
        total_pages = None
        while True:
            try:
                resp = self.get(URL, params={"page": page})
            except Exception as e:
                logger.warning("OK page %d failed: %s", page, e)
                break
            try:
                data = resp.json()
            except ValueError:
                break
            if total_pages is None:
                total_pages = data.get("TotalPages", 0) or 0
            winners = data.get("Winners") or []
            if not winners:
                break
            stale = 0
            for w in winners:
                norm = self._normalize(w)
                if not norm:
                    continue
                if norm["claim_date"] and norm["claim_date"] < cutoff:
                    stale += 1
                    continue
                if norm["source_id"] in seen:
                    continue
                seen.add(norm["source_id"])
                out.append(norm)
            if stale == len(winners) and stale > 0:
                break
            if page >= total_pages:
                break
            page += 1
        return out

    def _normalize(self, w: dict) -> dict | None:
        try:
            prize = float(w.get("Amount") or 0)
        except (TypeError, ValueError):
            return None
        if prize < self.min_prize:
            return None
        game = (w.get("GameName") or "").strip()
        if not game:
            return None
        if is_draw_game(self.state_code, game):
            return None
        city = (w.get("City") or "").strip().title() or None
        if not city:
            return None
        claim_date = _parse_dotnet_date(w.get("DateWon") or "")
        name = (w.get("Name") or "").strip()

        sid_parts = [
            claim_date.isoformat() if claim_date else "",
            name, city, game, f"{int(prize)}",
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
            "source_url": "https://www.lottery.ok.gov/winners/",
        }
