"""
Vermont Lottery scratch-off scraper.
Listing: https://www.vtlottery.com/games/instant-tickets  (server-rendered, ~87 games)
Detail:  https://www.vtlottery.com/{slug}

Detail page data (embedded in text, not a table):
  Game #, Ticket Price, Start Date, Overall Odds 1 in X
  # Of Tickets (total printed), % of Tickets Sold
  Unclaimed Top Prizes: $prize count $prize count ...
  Total Unclaimed $amount

EV method: EV = Total Unclaimed / tickets_remaining - price
  tickets_remaining = total_tickets * (1 - pct_sold / 100)
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

LIST_URL = "https://www.vtlottery.com/games/instant-tickets"
BASE_URL = "https://www.vtlottery.com"

# Nav/utility paths to skip when following links from listing
_SKIP_PATHS = {
    "/nolink", "/promos", "/where-to-play", "/giving-back", "/about",
    "/games", "/winners", "/win", "/contact", "/responsible-gambling",
    "/big-money", "/lottery-results", "/second-chance",
}


class VermontScraper(BaseScraper):
    state_code = "VT"
    state_name = "Vermont"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        games = []
        seen = set()

        # Iterate all pages (?page=0 through ?page=7, 12 games each)
        page = 0
        while True:
            url = f"{LIST_URL}?page={page}"
            try:
                soup = self.soup(url)
            except Exception as e:
                logger.debug("VT listing page %d error: %s", page, e)
                break

            new_slugs = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not re.match(r"^/[a-z][a-z0-9-]+$", href):
                    continue
                if href in _SKIP_PATHS:
                    continue
                if href in seen:
                    continue
                seen.add(href)
                new_slugs.append(href)

            if not new_slugs:
                break  # no new games on this page

            for href in new_slugs:
                slug = href.lstrip("/")
                detail_url = BASE_URL + href
                try:
                    game = self._scrape_detail(slug, detail_url)
                    if game:
                        games.append(game)
                except Exception as e:
                    logger.debug("VT detail error for %s: %s", slug, e)

            page += 1
            if page > 10:  # safety cap
                break

        logger.info("VT: %d games scraped across %d pages", len(games), page)
        return games

    def _scrape_detail(self, slug: str, url: str) -> dict | None:
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        # Must have ticket price to be a valid game page
        if "ticket price" not in page_text.lower():
            return None

        # Name from h1
        name_el = soup.select_one("h1")
        name = name_el.get_text(strip=True) if name_el else slug.replace("-", " ").title()
        if not name or len(name) < 3:
            return None

        # Remove duplicate h1 text (VT has it twice)
        if name.count(name[:10]) > 1:
            name = name[: len(name) // 2].strip()

        # Price
        price_m = re.search(r"Ticket\s+Price\s*\$?([\d.]+)", page_text, re.IGNORECASE)
        price = float(price_m.group(1)) if price_m else None
        if not price:
            return None

        # Game number
        game_num_m = re.search(r"Game\s*#\s*(\d+)", page_text, re.IGNORECASE)
        game_number = game_num_m.group(1) if game_num_m else None

        # Overall odds
        odds_m = re.search(r"Overall\s+Odds\s*1\s+in\s+([\d.]+)", page_text, re.IGNORECASE)
        overall_odds = float(odds_m.group(1)) if odds_m else None

        # Total tickets
        tickets_m = re.search(r"#\s+Of\s+Tickets\s+([\d,]+)", page_text, re.IGNORECASE)
        total_tickets = int(tickets_m.group(1).replace(",", "")) if tickets_m else None

        # % of tickets sold → tickets remaining
        sold_m = re.search(r"%\s+of\s+Tickets\s+Sold\s+(\d+)", page_text, re.IGNORECASE)
        pct_sold = int(sold_m.group(1)) if sold_m else None

        tickets_remaining = None
        if total_tickets and pct_sold is not None:
            tickets_remaining = round(total_tickets * (100 - pct_sold) / 100)

        # Unclaimed top prizes for informational display
        tiers = []
        top_section_m = re.search(
            r"Unclaimed\s+Top\s+Prizes\s+((?:\$[\d,]+\s+\d+\s*)+)",
            page_text, re.IGNORECASE
        )
        if top_section_m:
            section = top_section_m.group(1)
            for m in re.finditer(r"\$([\d,]+)\s+(\d+)", section):
                prize = float(m.group(1).replace(",", ""))
                remaining = int(m.group(2))
                if prize > 0 and overall_odds:
                    tiers.append({
                        "prize_amount":     prize,
                        "odds_one_in":      round(total_tickets / max(remaining, 1), 2) if total_tickets and remaining else overall_odds * 50,
                        "prizes_total":     None,
                        "prizes_remaining": remaining,
                    })

        if not price:
            return None

        # Total Unclaimed prize pool — use for EV calculation
        unclaimed_m = re.search(r"Total\s+Unclaimed\s+\$?([\d,]+(?:\.\d+)?)", page_text, re.IGNORECASE)
        total_unclaimed = float(unclaimed_m.group(1).replace(",", "")) if unclaimed_m else None

        ev = None
        return_pct = None
        if total_unclaimed and tickets_remaining and tickets_remaining > 0:
            ev = round(total_unclaimed / tickets_remaining - price, 4)
            return_pct = round(total_unclaimed / tickets_remaining / price * 100, 2)

        # Ticket image — look for the "front covered" shot in instant-tickets folder
        image_url = None
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if "instant-tickets" in src and src.lower().endswith((".jpg", ".png", ".webp")):
                image_url = BASE_URL + src if src.startswith("/") else src
                break

        top_prize = max((t["prize_amount"] for t in tiers), default=None)
        top_prize_remaining = next(
            (t["prizes_remaining"] for t in tiers if t["prize_amount"] == top_prize), None
        ) if top_prize else None

        return {
            "game_id":              game_number or slug,
            "name":                 name,
            "price":                price,
            "ev":                   ev,
            "return_pct":           return_pct,
            "overall_odds_one_in":  overall_odds,
            "top_prize":            top_prize,
            "top_prize_remaining":  top_prize_remaining,
            "total_tickets":        total_tickets,
            "tickets_remaining":    tickets_remaining,
            "prize_pool_left":      total_unclaimed,
            "detail_url":           url,
            "image_url":            image_url,
            "tiers":                tiers,
        }
