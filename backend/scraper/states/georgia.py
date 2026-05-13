"""
Georgia Lottery scratch-off scraper.
API: https://www.galottery.com/api/v1/instant-games/games?size=1000
  Fields: gameId, gameName, validationStatus, ticketPrice (cents),
          prizeTiers[]: prizeAmount (1/100 cent = divide by 10000 for dollars), winningTickets (total), paidTickets (claimed)

Total tickets not provided by API; estimated from prize pool assuming ~65% return rate.
EV: remaining_prize_pool / tickets_remaining - ticket_price
"""
from __future__ import annotations
import logging
import time
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

API_URL = "https://www.galottery.com/api/v1/instant-games/games?size=1000"
BASE_URL = "https://www.galottery.com"

_PAYOUT_RATE = 0.65  # GA lottery mandated return rate (approximate)
_HEADERS = {
    "Referer": "https://www.galottery.com/en-us/games/scratchers/active-games.html",
    "Accept": "application/json",
}


class GeorgiaScraper(BaseScraper):
    state_code = "GA"
    state_name = "Georgia"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(API_URL, headers=_HEADERS)
        data = resp.json()
        raw_games = data.get("games", []) if isinstance(data, dict) else data
        active = [g for g in raw_games if g.get("validationStatus") == "ACTIVE"]
        logger.info("GA: %d active games from API (of %d total)", len(active), len(raw_games))

        games = []
        for g in active:
            game = self._parse_game(g)
            if game:
                games.append(game)

        logger.info("GA: %d games parsed", len(games))
        return games

    def _parse_game(self, g: dict) -> dict | None:
        name = (g.get("gameName") or "").strip().title()
        if not name:
            return None

        game_id = str(g.get("gameId", ""))
        price_cents = g.get("ticketPrice") or 0
        price = price_cents / 100.0
        if not price:
            return None

        # GA API has no image fields; construct URL from game ID using the site's CDN pattern
        image_url = f"{BASE_URL}/content/dam/portal/images/scratchers-games/{game_id}/thumb.png" if game_id else None

        tiers_raw = g.get("prizeTiers") or []
        tiers = []
        total_prizes_printed = 0
        total_prizes_remaining = 0
        total_prize_value = 0.0

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
            total_prize_value += prize * total

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      None,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers:
            return None

        # Estimate total_tickets from prize pool and assumed payout rate
        total_tickets = None
        tickets_remaining = None
        if total_prize_value > 0 and price > 0:
            total_tickets = round(total_prize_value / (_PAYOUT_RATE * price))
            if total_prizes_printed > 0:
                fraction_rem = total_prizes_remaining / total_prizes_printed
                tickets_remaining = round(total_tickets * fraction_rem)
            for t in tiers:
                if t["prizes_total"] and total_tickets:
                    t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=f"{BASE_URL}/en-us/games/scratchers.html",
            image_url=image_url,
        )
