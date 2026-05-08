"""
Florida Lottery scratch-off scraper.
API: https://apim-website-prod-eastus.azure-api.net/scratchgamesapp/getscratchinfo
  Fields: Id, GameName, TicketPrice (dollars), OverallOdds, OddsTiers[]
  OddsTiers: PrizeAmount (string "$X.XX"), WinningOdds ("1-in-X"),
             TotalPrizes, PrizesRemaining, PrizesPaid
  total_tickets = sum(TotalPrizes) * OverallOdds
"""
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

API_URL = "https://apim-website-prod-eastus.azure-api.net/scratchgamesapp/getscratchinfo"
BASE_URL = "https://floridalottery.com"

_HEADERS = {
    "x-partner": "web",
    "Referer": "https://floridalottery.com/",
}


class FloridaScraper(BaseScraper):
    state_code = "FL"
    state_name = "Florida"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(API_URL, headers=_HEADERS)
        raw_games = resp.json()
        logger.info("FL: %d games from API", len(raw_games))

        games = []
        for g in raw_games:
            game = self._parse_game(g)
            if game:
                games.append(game)

        logger.info("FL: %d games parsed", len(games))
        return games

    def _parse_game(self, g: dict) -> dict | None:
        name = (g.get("GameName") or "").strip().title()
        if not name:
            return None

        game_id = str(g.get("Id", ""))
        price = g.get("TicketPrice") or 0
        if not price:
            return None

        overall_odds = g.get("OverallOdds") or None

        tiers_raw = g.get("OddsTiers") or []
        tiers = []
        total_prizes_printed = 0
        total_prizes_remaining = 0

        for t in tiers_raw:
            prize = parse_prize_amount(str(t.get("PrizeAmount") or ""))
            odds = parse_odds(str(t.get("WinningOdds") or ""))
            total = int(t.get("TotalPrizes") or 0)
            remaining = int(t.get("PrizesRemaining") or 0)

            if not prize or prize <= 0 or total <= 0:
                continue

            total_prizes_printed += total
            total_prizes_remaining += remaining

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      odds,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers:
            return None

        # Skip games with suspiciously low print runs (data not yet fully populated)
        if total_prizes_printed < 10_000:
            return None

        total_tickets = None
        tickets_remaining = None
        if overall_odds and overall_odds > 1.5 and total_prizes_printed > 0:
            total_tickets = round(overall_odds * total_prizes_printed)
            tickets_remaining = round(overall_odds * total_prizes_remaining)

        detail_url = f"{BASE_URL}/games/scratch-offs/view?id={game_id}"
        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=detail_url,
        )
