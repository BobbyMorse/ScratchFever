"""
Michigan Lottery scratch-off scraper.
Uses the Apollo GraphQL API directly — no Playwright needed.
  Catalog:  POST /api/prizes-remaining → getCMSGames (price, overall odds)
  Prizes:   POST /api/prizes-remaining → getRetailTopPrizesRemainingByGameType
  Joined on igtId (catalog) == cms_game_igt_id (prize data)
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

API_URL = "https://www.michiganlottery.com/api/prizes-remaining"
GAME_BASE = "https://www.michiganlottery.com/games"

CATALOG_QUERY = "{ getCMSGames { name igtId identifier overallOdds displayedTicketPrice canBuyInStore } }"
PRIZES_QUERY = ('{ getRetailTopPrizesRemainingByGameType(gameType: "instore-instant")'
                ' { cms_game_igt_id game_name prizesRemainingData'
                ' { prize_amount prizes_remaining starting_amount } } }')

_PRICE_RE = re.compile(r"\$([\d.]+)")
_ODDS_RE = re.compile(r"1\s+in\s+([\d,.]+)", re.I)


class MichiganScraper(BaseScraper):
    state_code = "MI"
    state_name = "Michigan"
    base_url = "https://www.michiganlottery.com"
    scraper_timeout = 120

    def _gql(self, query: str) -> dict:
        resp = self.session.post(
            API_URL,
            json={"query": query},
            headers={
                "Content-Type": "application/json",
                "apollo-require-preflight": "true",
                "Origin": "https://www.michiganlottery.com",
                "Referer": "https://www.michiganlottery.com/",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def scrape(self) -> list[dict]:
        catalog_resp = self._gql(CATALOG_QUERY)
        all_games = catalog_resp["data"]["getCMSGames"]
        catalog = {g["igtId"]: g for g in all_games if g.get("canBuyInStore")}
        logger.info("MI: catalog has %d canBuyInStore games", len(catalog))

        prize_resp = self._gql(PRIZES_QUERY)
        prize_games = prize_resp["data"]["getRetailTopPrizesRemainingByGameType"]
        logger.info("MI: prize API has %d games", len(prize_games))

        games = []
        for pg in prize_games:
            igt_id = pg["cms_game_igt_id"]
            cat = catalog.get(igt_id)
            if not cat:
                logger.debug("MI: no catalog entry for igtId=%s (%s)", igt_id, pg["game_name"])
                continue

            price = self._parse_price(cat.get("displayedTicketPrice", ""))
            if not price:
                continue

            overall_odds = self._parse_odds(cat.get("overallOdds", ""))
            name = cat.get("name") or pg["game_name"]
            identifier = cat.get("identifier", "")

            tiers = []
            for t in pg["prizesRemainingData"]:
                prize_amount = t.get("prize_amount")
                prizes_remaining = t.get("prizes_remaining")
                starting_amount = t.get("starting_amount")
                if prize_amount and prize_amount > 0:
                    tiers.append({
                        "prize_amount": float(prize_amount),
                        "odds_one_in": None,
                        "prizes_remaining": prizes_remaining,
                        "prizes_total": starting_amount,
                    })

            if not tiers:
                continue

            total_tickets = None
            tickets_remaining = None
            if overall_odds and overall_odds > 0:
                total_prizes_printed = sum(t["prizes_total"] or 0 for t in tiers)
                total_prizes_remaining = sum(t["prizes_remaining"] or 0 for t in tiers)
                if total_prizes_printed > 0:
                    total_tickets = round(overall_odds * total_prizes_printed)
                    if total_prizes_remaining > 0:
                        tickets_remaining = round(overall_odds * total_prizes_remaining)
                    for t in tiers:
                        if t["prizes_total"]:
                            t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

            slug = identifier.lower().replace("_", "-") if identifier else str(igt_id)
            detail_url = f"{GAME_BASE}/{slug}"

            raw_img = cat.get("imageUrl") or ""
            image_url = (("https://www.michiganlottery.com" + raw_img) if raw_img.startswith("/") else raw_img) or None

            game = self.build_game(
                game_id=str(igt_id),
                name=name,
                price=price,
                tiers=tiers,
                overall_odds=overall_odds,
                total_tickets=total_tickets,
                tickets_remaining=tickets_remaining,
                detail_url=detail_url,
                image_url=image_url,
            )
            games.append(game)

        logger.info("MI: %d games built", len(games))
        return games

    @staticmethod
    def _parse_price(s: str) -> float | None:
        m = _PRICE_RE.search(s)
        return float(m.group(1)) if m else None

    @staticmethod
    def _parse_odds(s: str) -> float | None:
        m = _ODDS_RE.search(s)
        return float(m.group(1).replace(",", "")) if m else None
