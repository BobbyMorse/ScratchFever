"""
Georgia Lottery scratch-off scraper.
API: https://www.galottery.com/api/v1/instant-games/games?size=1000
  Fields: gameId, gameName, validationStatus, ticketPrice (cents),
          prizeTiers[]: prizeAmount (1/100 cent = divide by 10000 for dollars),
          winningTickets (total printed), paidTickets (claimed)

Overall odds are scraped from each game's detail page:
  https://www.galottery.com/en-us/games/scratchers/{gameId}.html
  Pattern: "Overall odds of winning ... are 1 in X.XX."
  This lets us derive tickets_remaining and compute EV.
"""
from __future__ import annotations
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from backend.scraper.base import BaseScraper, HEADERS

logger = logging.getLogger(__name__)

API_URL = "https://www.galottery.com/api/v1/instant-games/games?size=1000&page=0"
BASE_URL = "https://www.galottery.com"

_API_HEADERS = {
    **HEADERS,
    "Referer": "https://www.galottery.com/en-us/games/scratchers/active-games.html",
    "Accept": "application/json",
}
_DETAIL_CONCURRENCY = 10
_DETAIL_TIMEOUT = 15


def _fetch_overall_odds(game_id: str) -> float | None:
    url = f"{BASE_URL}/en-us/games/scratchers/{game_id}.html"
    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Referer": BASE_URL + "/en-us/games/scratchers/active-games.html"},
            timeout=_DETAIL_TIMEOUT,
        )
        resp.raise_for_status()
        m = re.search(r"1 in (\d+(?:\.\d+)?)", resp.text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    except Exception as exc:
        logger.warning("GA: odds fetch failed for game %s: %s", game_id, exc)
    return None


class GeorgiaScraper(BaseScraper):
    state_code = "GA"
    state_name = "Georgia"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(API_URL, headers=_API_HEADERS)
        data = resp.json()
        raw_games = data.get("games", []) if isinstance(data, dict) else data
        now_ms = time.time() * 1000
        active = [
            g for g in raw_games
            if g.get("validationStatus") == "ACTIVE"
            and (g.get("disableDate") or 0) > now_ms
        ]
        logger.info("GA: %d active games from API (of %d total)", len(active), len(raw_games))

        game_ids = [str(g.get("gameId", "")) for g in active if g.get("gameId")]
        odds_map: dict[str, float | None] = {}

        with ThreadPoolExecutor(max_workers=_DETAIL_CONCURRENCY) as executor:
            future_to_id = {executor.submit(_fetch_overall_odds, gid): gid for gid in game_ids}
            for future in as_completed(future_to_id):
                odds_map[future_to_id[future]] = future.result()

        fetched = sum(1 for v in odds_map.values() if v is not None)
        logger.info("GA: fetched overall odds for %d/%d games", fetched, len(game_ids))

        games = []
        for g in active:
            game = self._parse_game(g, odds_map)
            if game:
                games.append(game)

        logger.info("GA: %d games parsed", len(games))
        return games

    def _parse_game(self, g: dict, odds_map: dict) -> dict | None:
        name = (g.get("gameName") or "").strip().title()
        if not name:
            return None

        game_id = str(g.get("gameId", ""))
        price_cents = g.get("ticketPrice") or 0
        price = price_cents / 100.0
        if not price:
            return None

        image_url = (
            f"{BASE_URL}/content/dam/portal/images/scratchers-games/{game_id}/thumb.png"
            if game_id else None
        )

        tiers_raw = g.get("prizeTiers") or []
        tiers = []
        total_prizes_printed = 0
        total_prizes_remaining = 0

        for t in tiers_raw:
            prize_cents = t.get("prizeAmount") or 0
            prize = prize_cents / 10000.0
            total = int(t.get("winningTickets") or 0)
            paid = int(t.get("paidTickets") or 0)
            remaining = max(total - paid, 0)

            if prize <= 0 or total <= 0:
                continue

            total_prizes_printed += total
            total_prizes_remaining += remaining

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      None,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers:
            return None

        # Derive tickets_remaining from overall odds + prize claim rate.
        # total_tickets_printed = prizes_printed * overall_odds
        # tickets_remaining ≈ total_tickets_printed * (prizes_remaining / prizes_printed)
        overall_odds = odds_map.get(game_id)
        tickets_remaining = None
        total_tickets = None
        if overall_odds and total_prizes_printed > 0:
            total_tickets = round(total_prizes_printed * overall_odds)
            remaining_frac = total_prizes_remaining / total_prizes_printed
            tickets_remaining = round(total_tickets * remaining_frac)

        end_date = None
        disable_ms = g.get("disableDate")
        if disable_ms:
            end_date = datetime.fromtimestamp(disable_ms / 1000, tz=timezone.utc).date().isoformat()

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            tickets_remaining=tickets_remaining,
            total_tickets=total_tickets,
            overall_odds=overall_odds,
            detail_url=f"{BASE_URL}/en-us/games/scratchers/{game_id}.html",
            image_url=image_url,
            end_date=end_date,
        )
