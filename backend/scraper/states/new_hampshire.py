"""
New Hampshire Lottery scratch-off scraper.
The NH Lottery exposes a public JSON API that returns all active games
in a single request — no Playwright needed.
"""
from __future__ import annotations
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

GAME_LIST_URL = "https://www.nhlottery.com/api/v1/game/all?platform=web&cmsPreview=false"
BASE_URL = "https://www.nhlottery.com"


class NewHampshireScraper(BaseScraper):
    state_code = "NH"
    state_name = "New Hampshire"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(GAME_LIST_URL, headers={"Accept": "application/json"})
        data = resp.json()
        raw_games = data.get("data", {}).get("games", [])

        games = []
        seen: set[str] = set()
        for raw in raw_games:
            if raw.get("type") != "scratch":
                continue
            game = self._parse_game(raw)
            if game and game["game_id"] not in seen:
                seen.add(game["game_id"])
                games.append(game)

        logger.info("NH: %d scratch games scraped", len(games))
        return games

    def _parse_game(self, raw: dict) -> dict | None:
        game_id = (raw.get("identifier") or "").strip()
        if not game_id:
            return None

        name = (raw.get("name") or "").strip()
        if not name:
            return None

        price_obj = raw.get("price")
        if isinstance(price_obj, dict):
            cents = price_obj.get("priceInCents")
            price = float(cents) / 100.0 if cents else None
        elif isinstance(price_obj, (int, float)):
            price = float(price_obj)
        else:
            price = None
        if not price:
            return None

        image_url = (raw.get("imageUrl") or "").strip() or None
        overall_odds = raw.get("odds")
        total_tickets = raw.get("ticketsOrdered")
        detail_url = f"{BASE_URL}/scratch-tickets/{game_id}"

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=[],
            overall_odds=float(overall_odds) if overall_odds else None,
            total_tickets=int(total_tickets) if total_tickets else None,
            tickets_remaining=None,
            detail_url=detail_url,
            image_url=image_url,
        )
