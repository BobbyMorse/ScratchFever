"""
New Hampshire Lottery scratch-off scraper.
Game list from the public NH Lottery CMS API; remaining prize counts from the
Gambyt game-data service (API key embedded in the NH Lottery JS bundle); per-tier
original odds from NH's own prize-table endpoint.
"""
from __future__ import annotations
import logging
from collections import defaultdict

import requests

from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

GAME_LIST_URL = "https://www.nhlottery.com/api/v1/game/all?platform=web&cmsPreview=false"
PRIZES_URL = "https://prod.game-data.gambytservices.com/v1/instant-game/prizes-remaining"
PRIZE_TABLE_URL = "https://www.nhlottery.com/api/v1/game/prize-table"
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

    def _fetch_odds_by_amount(self, identifier: str) -> dict[float, float]:
        """Fetch the per-game Odds Breakdown table and return {prize_amount -> odds_one_in}.
        Multiple table rows can pay the same dollar amount (different winning
        patterns); we combine them: every row has prizes_i * odds_i == total
        tickets in the game, so the combined odds for an amount is
        total_tickets / sum(prizes_i)."""
        try:
            resp = self.get(
                PRIZE_TABLE_URL,
                params={"identifier": identifier},
                headers={"Accept": "application/json"},
            )
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.debug("NH: prize-table fetch failed for %s: %s", identifier, exc)
            return {}

        if payload.get("status") != "success":
            return {}
        table = (payload.get("data") or {}).get("prizeTable") or {}
        headers = [str(h).strip().lower() for h in table.get("headers", [])]
        rows = table.get("rows") or []

        def find_col(*keywords: str) -> int | None:
            for i, h in enumerate(headers):
                if any(k in h for k in keywords):
                    return i
            return None

        win_idx = find_col("win")
        count_idx = find_col("prizes in game", "prizes")
        odds_idx = find_col("odds")
        if None in (win_idx, count_idx, odds_idx):
            return {}

        agg: dict[float, dict[str, float]] = defaultdict(lambda: {"prizes": 0, "tickets": 0.0, "n": 0})
        for row in rows:
            if max(win_idx, count_idx, odds_idx) >= len(row):
                continue
            win_txt = str(row[win_idx]).replace("\t", "").replace(",", "").replace("$", "").strip()
            if not win_txt or "total" in win_txt.lower():
                continue
            count_txt = str(row[count_idx]).replace("\t", "").replace(",", "").strip()
            odds_txt = str(row[odds_idx]).replace("\t", "").replace(",", "").strip()
            try:
                amount = float(win_txt)
                prizes = int(float(count_txt))
                odds = float(odds_txt)
            except ValueError:
                continue
            if amount <= 0 or prizes <= 0 or odds <= 0:
                continue
            entry = agg[amount]
            entry["prizes"] += prizes
            entry["tickets"] += prizes * odds
            entry["n"] += 1

        out: dict[float, float] = {}
        for amount, entry in agg.items():
            if entry["prizes"] <= 0 or entry["n"] <= 0:
                continue
            avg_total_tickets = entry["tickets"] / entry["n"]
            out[amount] = round(avg_total_tickets / entry["prizes"], 2)
        return out

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
