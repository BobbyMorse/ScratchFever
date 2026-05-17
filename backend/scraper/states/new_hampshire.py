"""
New Hampshire Lottery scratch-off scraper.
Game list from the public NH Lottery CMS API; prize tiers from the
Gambyt game-data service (API key embedded in the NH Lottery JS bundle).
"""
from __future__ import annotations
import logging
from collections import defaultdict
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

GAME_LIST_URL = "https://www.nhlottery.com/api/v1/game/all?platform=web&cmsPreview=false"
PRIZES_URL = "https://prod.game-data.gambytservices.com/v1/instant-game/prizes-remaining"
GAME_DATA_API_KEY = "1c4c69db-274c-4f59-95c5-3211cd74e9d8"
BASE_URL = "https://www.nhlottery.com"


class NewHampshireScraper(BaseScraper):
    state_code = "NH"
    state_name = "New Hampshire"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        prizes_by_game = self._fetch_prizes()

        resp = self.get(GAME_LIST_URL, headers={"Accept": "application/json"})
        raw_games = resp.json().get("data", {}).get("games", [])

        games = []
        seen: set[str] = set()
        for raw in raw_games:
            if raw.get("type") != "scratch":
                continue
            game = self._parse_game(raw, prizes_by_game)
            if game and game["game_id"] not in seen:
                seen.add(game["game_id"])
                games.append(game)

        logger.info("NH: %d scratch games scraped", len(games))
        return games

    def _fetch_prizes(self) -> dict[str, list[dict]]:
        """Return {instantGameId -> list of tier dicts} from the Gambyt API."""
        resp = self.get(PRIZES_URL, headers={"X-API-Key": GAME_DATA_API_KEY})
        entries = resp.json().get("prizesRemaining", [])
        by_game: dict[str, list[dict]] = defaultdict(list)
        for e in entries:
            gid = e.get("instantGameId")
            if not gid:
                continue
            by_game[gid].append({
                "prize_amount": float(e["prizeAmountInDollars"]),
                "odds_one_in": None,
                "prizes_total": int(e["startingCount"]),
                "prizes_remaining": int(e["remainingCount"]),
            })
        return dict(by_game)

    def _parse_game(self, raw: dict, prizes_by_game: dict) -> dict | None:
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

        svc_id = (raw.get("configuration") or {}).get("dataServices", {}).get("gameDataServiceId")
        tiers = prizes_by_game.get(svc_id, []) if svc_id else []

        tickets_remaining = None
        if overall_odds and tiers:
            total_remaining = sum(t["prizes_remaining"] for t in tiers)
            total_starting = sum(t["prizes_total"] for t in tiers)
            if total_starting > 0:
                total_tickets = round(float(overall_odds) * total_starting)
            if total_remaining > 0:
                tickets_remaining = round(float(overall_odds) * total_remaining)

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=float(overall_odds) if overall_odds else None,
            total_tickets=int(total_tickets) if total_tickets else None,
            tickets_remaining=tickets_remaining,
            detail_url=detail_url,
            image_url=image_url,
        )
