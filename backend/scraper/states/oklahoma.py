"""
Oklahoma Lottery scratch-off scraper.

Second-chance: OK runs themed per-game promotions (50X Wild, JAWS,
Frogger, etc.) under /promotions but no recurring per-game flag system —
neither the Players Club portal nor the scratcher detail pages carry
the promo tie-in. has_second_chance stays FALSE for all OK games until
a per-game source is found.

lottery.ok.gov publishes the full active-games dataset as plain JSON at:

  GET /scratchers/get
    → {"Games":[
         {"Id":492142,"GameId":837,"IsActive":true,"Price":5,
          "OverallOdds":"3.09","Name":"Frogger","TicketsPrinted":844140,
          "Prizes":[{"PrizeAmount":5,"TotalPrizes":154741,
                     "RemainingPrizes":144211,"PrizeOdds":0}, ...]},
         ...
       ]}

Single 126KB request, no auth, no Playwright. Previous scraper was a
Playwright-driven response interceptor that recursively scanned JSON for
id_keys, plus per-game tab-clicks across SCRATCHER / ODDS / PRIZES
REMAINING. Brittle and slow (~600s timeout); fell over in mid-2026 when
the regex-only ID extraction at the listing page stopped matching, leaving
zero games in the DB.
"""
from __future__ import annotations
import logging

import requests

from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_odds

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lottery.ok.gov"
LIST_URL = f"{BASE_URL}/scratchers/get"
# Their Vue bundle builds image URLs as `lccontent/games/inserts/{PrimaryImage}.jpg`,
# where `lccontent` is an internal placeholder rewritten to this CDN host.
IMAGE_BASE = "https://content.lottery.ok.gov/games/inserts"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json",
    "Referer": BASE_URL + "/scratchers",
}


class OklahomaScraper(BaseScraper):
    state_code = "OK"
    state_name = "Oklahoma"
    base_url = BASE_URL
    scraper_timeout = 90  # was 600; pure HTTP completes in <5s

    def scrape(self) -> list[dict]:
        try:
            resp = requests.get(LIST_URL, headers=_HEADERS, timeout=60)
        except requests.RequestException as e:
            logger.warning("OK: list fetch error: %s", e)
            return []
        if resp.status_code != 200:
            logger.warning("OK: list HTTP %d", resp.status_code)
            return []

        try:
            data = resp.json()
        except ValueError:
            logger.warning("OK: list response not JSON")
            return []

        raw_games = data.get("Games") or []
        logger.info("OK: %d raw games from API", len(raw_games))

        games: list[dict] = []
        for r in raw_games:
            if not isinstance(r, dict) or not r.get("IsActive") or r.get("GameEnded"):
                continue
            built = self._build(r)
            if built:
                games.append(built)

        logger.info("OK: %d active games parsed", len(games))
        return games

    def _build(self, r: dict) -> dict | None:
        name = str(r.get("Name") or "").strip()
        if not name:
            return None

        game_number = str(r.get("GameId") or "").strip()
        if not game_number:
            return None

        try:
            price = float(r.get("Price") or 0)
        except (TypeError, ValueError):
            price = 0
        if not price:
            return None

        overall_odds = parse_odds(str(r.get("OverallOdds") or "")) or None

        tiers = self._parse_tiers(r.get("Prizes") or [])
        if not tiers:
            return None

        total_tickets = r.get("TicketsPrinted") if isinstance(r.get("TicketsPrinted"), int) else None

        # API only reports tier-level RemainingPrizes; derive ticket-level
        # remaining via overall_odds * sum(remaining tier prizes), same formula
        # IL/MA/WA use.
        total_remaining_prizes = sum(t.get("prizes_remaining") or 0 for t in tiers)
        tickets_remaining = (
            round(overall_odds * total_remaining_prizes)
            if overall_odds and total_remaining_prizes > 0
            else None
        )

        # If TicketsPrinted is absent, derive it from overall_odds * sum(prizes_total)
        if total_tickets is None and overall_odds:
            total_printed_prizes = sum(t.get("prizes_total") or 0 for t in tiers)
            if total_printed_prizes > 0:
                total_tickets = round(overall_odds * total_printed_prizes)

        image_key = str(r.get("PrimaryImage") or r.get("ThumbnailImage") or game_number).strip()
        image_url = f"{IMAGE_BASE}/{image_key}.jpg" if image_key else None

        return self.build_game(
            game_id=game_number,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=f"{BASE_URL}/scratchers/{r.get('Id') or game_number}",
            image_url=image_url,
        )

    @staticmethod
    def _parse_tiers(prize_rows: list) -> list[dict]:
        tiers = []
        for row in prize_rows or []:
            if not isinstance(row, dict):
                continue
            try:
                prize = float(row.get("PrizeAmount") or 0)
            except (TypeError, ValueError):
                continue
            if prize <= 0:
                continue
            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      None,  # PrizeOdds is consistently 0 in the API
                "prizes_total":     int(row.get("TotalPrizes") or 0) or None,
                "prizes_remaining": int(row.get("RemainingPrizes") or 0),
            })
        return tiers
