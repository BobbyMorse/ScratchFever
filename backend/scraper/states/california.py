"""
California Lottery scratch-off scraper.
API: https://www.calottery.com/api/games/scratchers
  Fields: gameNumber, name, price (int), cashOdds (overall odds denominator),
          prizeTiers[]: value (dollars), odds (denominator), totalNumberOfPrizes,
                        numberOfPrizesCashed, numberOfPrizesPending
  NOTE: The `number` field is a tier identifier, NOT prizes remaining.
        Prizes remaining = totalNumberOfPrizes - numberOfPrizesCashed - numberOfPrizesPending.
"""
from __future__ import annotations
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

API_URL = "https://www.calottery.com/api/games/scratchers"
BASE_URL = "https://www.calottery.com"


class CaliforniaScraper(BaseScraper):
    state_code = "CA"
    state_name = "California"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(API_URL)
        data = resp.json()
        raw_games = data.get("games", []) if isinstance(data, dict) else data
        active = [g for g in raw_games if g.get("state") == "Active"]
        logger.info("CA: %d active games from API (of %d total)", len(active), len(raw_games))

        games = []
        for g in active:
            game = self._parse_game(g)
            if game:
                games.append(game)

        logger.info("CA: %d games parsed", len(games))
        return games

    def _parse_game(self, g: dict) -> dict | None:
        name = (g.get("name") or g.get("marketingTitle") or "").strip()
        if not name:
            return None

        game_id = str(g.get("gameNumber") or "")
        price_raw = g.get("price") or ""
        price = parse_prize_amount(str(price_raw))
        if not price:
            return None

        overall_odds = None
        try:
            overall_odds = float(g.get("cashOdds") or 0) or None
        except (ValueError, TypeError):
            pass

        product_page = g.get("productPage") or ""
        detail_url = (BASE_URL + product_page) if product_page.startswith("/") else (product_page or BASE_URL)

        image_url = None
        raw_img = (
            g.get("cardImage") or g.get("unScratchedImage") or
            g.get("imageUrl") or g.get("thumbnailUrl") or g.get("image") or
            g.get("img") or g.get("gameImage") or g.get("ticketImage") or ""
        )
        if raw_img:
            image_url = (BASE_URL + raw_img) if raw_img.startswith("/") else raw_img
        elif game_id:
            image_url = f"https://www.calottery.com/api/games/scratchers/{game_id}/image"

        how_to_play = g.get("howToPlay") or None

        tiers_raw = g.get("prizeTiers") or []
        tiers = []
        total_prizes_printed = 0
        total_prizes_remaining = 0
        any_remaining_data = False

        for t in tiers_raw:
            prize = float(t.get("value") or 0)
            if prize <= 0:
                continue
            odds = float(t.get("odds") or 0) or None
            total = int(t.get("totalNumberOfPrizes") or 0)
            cashed = int(t.get("numberOfPrizesCashed") or 0)
            pending = int(t.get("numberOfPrizesPending") or 0)
            # `number` is a tier identifier, not prize count — compute real remaining
            remaining = max(0, total - cashed - pending)

            if remaining > 0:
                any_remaining_data = True
                total_prizes_remaining += remaining

            if total <= 0:
                continue

            total_prizes_printed += total

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      odds,
                "prizes_total":     total,
                "prizes_remaining": remaining if remaining > 0 else None,
            })

        if not tiers:
            return None

        total_tickets = None
        tickets_remaining = None
        if overall_odds and overall_odds > 0 and total_prizes_printed > 0:
            total_tickets = round(overall_odds * total_prizes_printed)
            # Only store tickets_remaining when the API provides real remaining data.
            # CA returns number=0 for all tiers when remaining counts are unavailable.
            if any_remaining_data:
                tickets_remaining = round(overall_odds * total_prizes_remaining)

        return self.build_game(
            game_id=game_id or name,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=detail_url,
            image_url=image_url,
        )
