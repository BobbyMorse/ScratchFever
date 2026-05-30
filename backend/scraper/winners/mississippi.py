"""
Mississippi winners scraper.

MS Lottery's winners archive is a WordPress paginated listing at:
  https://www.mslottery.com/winners/?paged=N
Each winner is its own post; titles follow one of:
  "<City> (Man|Woman|Couple|Family|Player) Wins $<amount>[k] [on <Game>]"
  "<Name> of <City> Wins $<amount>"

We extract amount + winner home city from the title text. No retailer info exposed
in the listing. The MS lottery is new (launched 2019) so volume is modest.
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.mslottery.com/winners/"

POST_RE = re.compile(
    r'itemid="(https://www\.mslottery\.com/[^"]+)"[^>]*content="([^"]+?Wins\s+\$[^"]+)"'
    r'[\s\S]{0,800}?datePublished"\s+content="([^"]+)"',
    re.IGNORECASE,
)
TITLE_CITY_PERSON_RE = re.compile(
    r'^([A-Z][A-Za-z\.\-\s]+?)\s+(?:Man|Woman|Couple|Family|Player|Resident)\s+Wins\s+\$([\d,.]+)(k)?',
    re.IGNORECASE,
)
TITLE_OF_CITY_RE = re.compile(
    r'^[A-Za-z\.\s]+\s+of\s+([A-Z][A-Za-z\.\-\s]+?)\s+Wins\s+\$([\d,.]+)(k)?',
    re.IGNORECASE,
)
GAME_RE = re.compile(r'\bon\s+([A-Z0-9][\w\$\&\s\']+?)(?:\s+scratch-?off)?\s*$', re.IGNORECASE)


def _parse_amount(num: str, k_suffix: str | None) -> float | None:
    try:
        n = float(num.replace(",", ""))
    except ValueError:
        return None
    if k_suffix and k_suffix.lower() == "k":
        return n * 1_000
    return n


class MississippiWinnersScraper(WinnersScraper):
    state_code = "MS"
    state_name = "Mississippi"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        out: list[dict] = []
        seen: set[str] = set()
        page = 1
        while page < 30:
            try:
                resp = self.get(URL, params={"paged": page})
            except Exception as e:
                logger.warning("MS page %d failed: %s", page, e)
                break
            matches = list(POST_RE.finditer(resp.text))
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
        url, title, date_iso = m.groups()
        # Try city-person form, then "of city" form
        city = None
        prize = None
        cm = TITLE_CITY_PERSON_RE.match(title)
        if cm:
            city = cm.group(1).strip().title()
            prize = _parse_amount(cm.group(2), cm.group(3))
        else:
            cm = TITLE_OF_CITY_RE.match(title)
            if cm:
                city = cm.group(1).strip().title()
                prize = _parse_amount(cm.group(2), cm.group(3))
        if not city or not prize:
            return None
        # Strip out-of-state qualifiers like "Slidell, La." — keep MS only.
        if "," in city:
            return None
        if prize < self.min_prize:
            return None
        gm = GAME_RE.search(title)
        game = gm.group(1).strip() if gm else "Scratch-Off"
        if is_draw_game(self.state_code, game):
            return None
        try:
            claim_date = dt.datetime.strptime(date_iso[:10], "%Y-%m-%d").date()
        except ValueError:
            claim_date = None
        sid = url.rstrip("/").split("/")[-1]
        return {
            "source_id": sid,
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
            "source_url": url,
        }
