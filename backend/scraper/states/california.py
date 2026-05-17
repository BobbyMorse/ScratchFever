"""
California Lottery scratch-off scraper.
API: https://www.calottery.com/api/games/scratchers
  Fields: gameNumber, name, price (int), cashOdds (cash-prize odds denominator),
          prizeTiers[]: value (dollars), odds (per-tier denominator), totalNumberOfPrizes,
                        numberOfPrizesCashed, numberOfPrizesPending
  NOTE: numberOfPrizesPending = prizes still in the field (not yet claimed).
        prizes_remaining = totalNumberOfPrizes - numberOfPrizesCashed (= numberOfPrizesPending).
        tickets_remaining is estimated via median(prizes_remaining_i * odds_i) across tiers.
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
        any_remaining_data = False

        for t in tiers_raw:
            prize = float(t.get("value") or 0)
            if prize <= 0:
                continue
            odds = float(t.get("odds") or 0) or None
            total = int(t.get("totalNumberOfPrizes") or 0)
            cashed = int(t.get("numberOfPrizesCashed") or 0)
            # numberOfPrizesPending = prizes still in the field (not yet claimed)
            remaining = max(0, total - cashed)

            if total <= 0:
                continue

            if remaining > 0:
                any_remaining_data = True

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      odds,
                "prizes_total":     total,
                "prizes_remaining": remaining if remaining > 0 else None,
            })

        if not tiers:
            return None

        # Use per-tier odds for accurate ticket estimates (CA provides per-tier odds explicitly)
        total_estimates = [
            round(t["prizes_total"] * t["odds_one_in"])
            for t in tiers
            if t["prizes_total"] and t.get("odds_one_in") and t["odds_one_in"] > 0
        ]
        remaining_estimates = [
            round(t["prizes_remaining"] * t["odds_one_in"])
            for t in tiers
            if t["prizes_remaining"] and t.get("odds_one_in") and t["odds_one_in"] > 0
        ]

        total_tickets = None
        tickets_remaining = None
        if total_estimates:
            total_estimates.sort()
            total_tickets = total_estimates[len(total_estimates) // 2]
        if remaining_estimates and any_remaining_data:
            remaining_estimates.sort()
            tickets_remaining = remaining_estimates[len(remaining_estimates) // 2]

        game = self.build_game(
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
        game["how_to_play"] = how_to_play
        return game
