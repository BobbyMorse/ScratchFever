"""
New Jersey Lottery scratch-off scraper.
API: https://www.njlottery.com/api/v2/instant-games/games?size=500
  Returns all games (active + expired); filter by endDistributionDate > now_ms.
  Amounts in CENTS: ticketPrice, prizeTiers[].prizeAmount
  prizeTiers[]: prizeAmount, winningTickets (total), paidTickets (claimed)
  totalTicketsPrinted: total tickets for the game
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

API_URL = "https://www.njlottery.com/api/v2/instant-games/games?size=500"
BASE_URL = "https://www.njlottery.com"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.njlottery.com/en-us/games/scratchers.html",
    "Accept": "application/json",
}


class NewJerseyScraper(BaseScraper):
    state_code = "NJ"
    state_name = "New Jersey"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(API_URL, headers=_HEADERS)
        data = resp.json()
        raw_games = data.get("games", [])
        now_ms = time.time() * 1000
        active = [
            g for g in raw_games
            if g.get("gameId") and g.get("validationStatus") == "ACTIVE"
            and (g.get("disableDate") or 0) > now_ms
        ]
        logger.info("NJ: %d active games (of %d total)", len(active), len(raw_games))

        games = []
        for g in active:
            game = self._parse_game(g)
            if game:
                games.append(game)

        logger.info("NJ: %d games parsed", len(games))
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

        raw_total = g.get("totalTicketsPrinted")
        total_tickets = int(raw_total) if raw_total else None

        # Image URL: check common field names in the NJ API response
        image_url = None
        raw_img = (
            g.get("imageUrl") or g.get("image") or g.get("thumbnailUrl") or
            g.get("gameImage") or g.get("ticketImage") or g.get("img") or ""
        )
        if raw_img:
            image_url = (BASE_URL + raw_img) if raw_img.startswith("/") else raw_img

        tiers = []
        total_prizes_printed = 0
        total_prizes_remaining = 0

        for t in (g.get("prizeTiers") or []):
            prize_cents = t.get("prizeAmount") or 0
            prize = prize_cents / 100.0
            total = int(t.get("winningTickets") or 0)
            paid = int(t.get("paidTickets") or 0)
            remaining = max(total - paid, 0)

            if prize <= 0 or total <= 0:
                continue

            odds_val = round(total_tickets / total, 2) if total_tickets and total > 0 else None

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      odds_val,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })
            total_prizes_printed += total
            total_prizes_remaining += remaining

        if not tiers:
            return None

        tickets_remaining = None
        if total_tickets and total_prizes_printed > 0:
            fraction_rem = total_prizes_remaining / total_prizes_printed
            tickets_remaining = round(total_tickets * fraction_rem)

        end_date = None
        end_ms = g.get("endDistributionDate")
        if end_ms:
            end_date = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date().isoformat()

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=f"{BASE_URL}/en-us/games/scratchers.html",
            image_url=image_url,
            end_date=end_date,
        )
