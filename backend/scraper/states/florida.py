"""
Florida Lottery scratch-off scraper.
API: https://apim-website-prod-eastus.azure-api.net/scratchgamesapp/getscratchinfo
  Fields: Id, GameName, TicketPrice (dollars), OverallOdds, OddsTiers[]
  OddsTiers: PrizeAmount (string "$X.XX"), WinningOdds ("1-in-X"),
             TotalPrizes, PrizesRemaining, PrizesPaid
  total_tickets = sum(TotalPrizes) * OverallOdds

Images: fetched from AEM CMS endpoint which returns teaserImage paths per game ID.
"""
from __future__ import annotations
import logging
from datetime import date
from backend.scraper.base import BaseScraper, FOR_LIFE_DEFAULT_YEARS, parse_for_life_tier
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

API_URL = "https://apim-website-prod-eastus.azure-api.net/scratchgamesapp/getscratchinfo"
AEM_URL = "https://floridalottery.com/content/flalottery-web/us/en/games/scratch-offs.scratch-offs.json"
BASE_URL = "https://floridalottery.com"

_HEADERS = {
    "x-partner": "web",
    "Referer": "https://floridalottery.com/",
}


class FloridaScraper(BaseScraper):
    state_code = "FL"
    state_name = "Florida"
    base_url = BASE_URL

    def _fetch_image_map(self) -> dict[str, str]:
        """Return {game_id: absolute_image_url} from the AEM scratch-offs JSON."""
        try:
            resp = self.session.get(AEM_URL, timeout=15)
            if resp.status_code != 200:
                logger.warning("FL AEM image endpoint returned %d", resp.status_code)
                return {}
            entries = resp.json().get("data", [])
            result = {}
            for entry in entries:
                gid = str(entry.get("id") or "")
                img = entry.get("teaserImage") or ""
                if gid and img:
                    result[gid] = (BASE_URL + img) if img.startswith("/") else img
            logger.info("FL: image map built for %d games", len(result))
            return result
        except Exception as e:
            logger.warning("FL: failed to fetch image map: %s", e)
            return {}

    def scrape(self) -> list[dict]:
        image_map = self._fetch_image_map()

        resp = self.get(API_URL, headers=_HEADERS)
        data = resp.json()
        raw_games = data.get("games", []) if isinstance(data, dict) else data
        today = date.today().isoformat()
        active = [g for g in raw_games if (g.get("EndDate") or "9999-01-01")[:10] >= today]
        logger.info("FL: %d active games from API (of %d total)", len(active), len(raw_games))

        games = []
        for g in active:
            game = self._parse_game(g, image_map)
            if game:
                games.append(game)

        logger.info("FL: %d games parsed", len(games))
        return games

    def _parse_game(self, g: dict, image_map: dict[str, str]) -> dict | None:
        name = (g.get("GameName") or "").strip().title()
        if not name:
            return None

        game_id = str(g.get("Id", ""))
        price = float(g.get("TicketPrice") or 0)
        if not price:
            return None

        overall_odds = g.get("OverallOdds") or None
        image_url = image_map.get(game_id)
        end_date = (g.get("EndDate") or "")[:10] or None

        tiers_raw = g.get("OddsTiers") or []
        tiers: list[dict] = []
        seen: set[tuple] = set()
        total_prizes_printed = 0
        total_prizes_remaining = 0
        any_remaining_data = False

        for t in tiers_raw:
            raw_prize = str(t.get("PrizeAmount") or "")
            prize = parse_prize_amount(raw_prize)
            odds = parse_odds(str(t.get("WinningOdds") or ""))
            total = int(t.get("TotalPrizes") or 0)
            raw_remaining = t.get("PrizesRemaining")
            remaining = int(raw_remaining or 0)
            if raw_remaining is not None and remaining > 0:
                any_remaining_data = True

            for_life = None
            if not prize or prize <= 0:
                # FL encodes for-life prizes as e.g. "$10,000/WK/LIFE",
                # "$2,500 WK/LIFE", "$50,000YR/LIFE" — non-numeric, drops silently
                # without targeted parsing.
                for_life = parse_for_life_tier(raw_prize)
                if not for_life:
                    continue
                prize = for_life[0]
            if total <= 0:
                continue

            key = (prize, total)
            if key in seen:
                continue
            seen.add(key)

            total_prizes_printed += total
            total_prizes_remaining += remaining

            tier = {
                "prize_amount":     prize,
                "odds_one_in":      odds,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            }
            if for_life:
                _, annual, cash = for_life
                tier["is_annuity"] = True
                tier["annuity_annual"] = annual
                tier["annuity_years"] = FOR_LIFE_DEFAULT_YEARS
                tier["cash_value"] = round(cash, 2)
            tiers.append(tier)

        if not tiers:
            return None

        # Skip games with suspiciously low print runs (data not yet fully populated)
        if total_prizes_printed < 10_000:
            return None

        total_tickets = None
        tickets_remaining = None
        if overall_odds and overall_odds > 1.5 and total_prizes_printed > 0:
            total_tickets = round(overall_odds * total_prizes_printed)
            if any_remaining_data:
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
            image_url=image_url,
            end_date=end_date,
        )
