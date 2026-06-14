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
from datetime import date, datetime
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

LIST_URL = "https://www.vtlottery.com/games/instant-tickets"
END_DATE_URL = "https://www.vtlottery.com/games/instant-tickets/last-day-to-redeem"
BASE_URL = "https://www.vtlottery.com"

# VT's 2nd Chance portal (separate subdomain) lists eligible scratchers
# with their game number in parens:
#   <p class="largeLabel">NAME $PRICE (GAME_NUMBER)</p>
# Game numbers join cleanly to vtlottery.com's scratcher numbering.
SECOND_CHANCE_URL = "https://vtlottery.2ndchanceplay.com/index.php?nav=eligibletickets"
_VT_SC_LABEL_RE = re.compile(r'largeLabel">[^<]+?\((\d+)\)</p>', re.I)

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

    def _fetch_end_date_map(self) -> dict[str, str]:
        """Return {game_number: end_date_iso} from the last-day-to-redeem page.
        Games with end_date in the past are filtered out by the caller."""
        result: dict[str, str] = {}
        try:
            soup = self.soup(END_DATE_URL)
            for row in soup.select("table tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 5:
                    continue
                game_num = cells[0].get_text(strip=True)
                game_end_text = cells[4].get_text(strip=True)
                if not game_num.isdigit() or not game_end_text or game_end_text.upper() == "TBD":
                    continue
                try:
                    parsed = datetime.strptime(game_end_text, "%m/%d/%y").date()
                    result[game_num] = parsed.isoformat()
                except ValueError:
                    try:
                        parsed = datetime.strptime(game_end_text, "%m/%d/%Y").date()
                        result[game_num] = parsed.isoformat()
                    except ValueError:
                        pass
            logger.info("VT: end date map built for %d games", len(result))
        except Exception as e:
            logger.warning("VT: failed to fetch end date map: %s", e)
        return result

    def scrape(self) -> list[dict]:
        end_date_map = self._fetch_end_date_map()
        sc_game_ids = self._fetch_second_chance_ids()
        logger.info("VT second-chance eligible game ids: %d", len(sc_game_ids))
        today = date.today().isoformat()
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
                    game = self._scrape_detail(slug, detail_url, end_date_map, today)
                    if game:
                        if str(game.get("game_id")) in sc_game_ids:
                            game["has_second_chance"] = True
                            game["second_chance_url"] = SECOND_CHANCE_URL
                        games.append(game)
                except Exception as e:
                    logger.debug("VT detail error for %s: %s", slug, e)

            page += 1
            if page > 10:  # safety cap
                break

        logger.info("VT: %d games scraped across %d pages", len(games), page)
        return games

    def _scrape_detail(self, slug: str, url: str, end_date_map: dict, today: str) -> dict | None:
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

        # End date from last-day-to-redeem page; skip if expired
        end_date = end_date_map.get(game_number) if game_number else None
        if end_date and end_date < today:
            logger.debug("VT: skipping expired game %s (end %s)", slug, end_date)
            return None

        # Overall odds — VT writes either "1:3.83" or "1 in 3.83"
        odds_m = re.search(r"Overall\s+Odds\s*1\s*(?::|in)\s*([\d.]+)", page_text, re.IGNORECASE)
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
                if prize > 0:
                    tiers.append({
                        "prize_amount":     prize,
                        "odds_one_in":      round(total_tickets / remaining, 2) if total_tickets and remaining else None,
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
            "end_date":             end_date,
            "tiers":                tiers,
            # VT publishes only aggregate "Total Unclaimed" + top-tier counts,
            # not per-tier remaining. EV is direct from the aggregate but the
            # per-tier breakdown is missing, so coverage map shows Estimated.
            "ev_approximate":       True,
        }
