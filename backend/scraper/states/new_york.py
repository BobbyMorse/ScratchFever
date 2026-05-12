"""
New York Lottery scratch-off scraper.
API: https://nylottery.ny.gov/drupal-api/api/scratch_off_games?_format=json&page=N
  Paginated (~6-18/page), fields: title, ticket_price ("5.00"), overall_odds ("1 in 4.35"),
  game_number, art[{uri, altText}], cropped_art{uri, altText},
  odds_prizes[{prize_amount, overall_odds, prizes_remaining, prizes_paid_out}]
"""
from __future__ import annotations
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

API_URL = "https://nylottery.ny.gov/drupal-api/api/scratch_off_games?_format=json"
BASE_URL = "https://nylottery.ny.gov"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://nylottery.ny.gov/scratch-off-games",
    "Accept": "application/json",
}


class NewYorkScraper(BaseScraper):
    state_code = "NY"
    state_name = "New York"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        games = []
        page = 0
        total_pages = 1

        while page < total_pages:
            try:
                resp = self.get(f"{API_URL}&page={page}", headers=_HEADERS)
                data = resp.json()
            except Exception as e:
                logger.error("NY page %d fetch failed: %s", page, e)
                break

            pager = data.get("pager", {})
            total_pages = int(pager.get("total_pages") or 1)
            rows = data.get("rows", [])

            for row in rows:
                game = self._parse_game(row)
                if game:
                    games.append(game)

            page += 1

        logger.info("NY: %d games parsed", len(games))
        return games

    def _parse_game(self, row: dict) -> dict | None:
        name = (row.get("title") or "").strip()
        if not name:
            return None

        game_id = str(row.get("nid") or row.get("game_number") or "")
        price = parse_prize_amount(str(row.get("ticket_price") or ""))
        if not price:
            return None

        overall_odds = parse_odds(str(row.get("overall_odds") or ""))
        alias = row.get("alias") or ""
        detail_url = (BASE_URL + alias) if alias.startswith("/") else BASE_URL

        # Image URL: Drupal APIs often return an image field or field_image
        image_url = None
        raw_img = (
            row.get("image") or row.get("field_image") or row.get("imageUrl") or
            row.get("image_url") or row.get("thumbnail") or row.get("game_image") or ""
        )
        if isinstance(raw_img, dict):
            raw_img = raw_img.get("url") or raw_img.get("src") or ""
        if raw_img:
            image_url = (BASE_URL + raw_img) if raw_img.startswith("/") else raw_img

        tiers_raw = row.get("odds_prizes") or []
        tiers = []
        total_prizes_printed = 0
        total_prizes_remaining = 0

        for t in tiers_raw:
            prize = parse_prize_amount(str(t.get("prize_amount") or ""))
            if not prize or prize <= 0:
                continue

            tier_odds = parse_odds(str(t.get("overall_odds") or ""))
            try:
                remaining = int(str(t.get("prizes_remaining") or "0").replace(",", ""))
            except (ValueError, TypeError):
                remaining = 0
            try:
                paid = int(str(t.get("prizes_paid_out") or "0").replace(",", ""))
            except (ValueError, TypeError):
                paid = 0

            total = remaining + paid
            total_prizes_printed += total
            total_prizes_remaining += remaining

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      tier_odds,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers:
            return None

        total_tickets = None
        tickets_remaining = None
        if overall_odds and overall_odds > 0 and total_prizes_printed > 0:
            total_tickets = round(overall_odds * total_prizes_printed)
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
