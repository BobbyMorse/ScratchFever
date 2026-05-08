"""
Maine State Lottery scratch-off scraper.
Listing pages: https://www.mainelottery.com/instant/scratch{N}dollar.html
  Links to individual game pages on maine.gov:
  https://www.maine.gov/tools/whatsnew/index.php?topic=Lottery_Scratch&id={ID}&v=article

Maine.gov game pages show: overall odds, total tickets printed.
No per-tier prize table is available, so EV is computed from overall odds only.
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import calculate_ev

logger = logging.getLogger(__name__)

BASE_URL = "https://www.mainelottery.com"
MAINE_GOV = "https://www.maine.gov"
PRICE_PAGES = [
    (1,  "/instant/scratch1dollar.html"),
    (2,  "/instant/scratch2dollar.html"),
    (3,  "/instant/scratch3dollar.html"),
    (5,  "/instant/scratch5dollar.html"),
    (10, "/instant/scratch10dollar.html"),
    (20, "/instant/scratch20dollar.html"),
    (25, "/instant/scratch25dollar.html"),
    (30, "/instant/scratch30dollar.html"),
]


class MaineScraper(BaseScraper):
    state_code = "ME"
    state_name = "Maine"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        games = []
        seen = set()

        for price, path in PRICE_PAGES:
            url = BASE_URL + path
            try:
                soup = self.soup(url)
            except Exception as e:
                logger.debug("ME listing error for %s: %s", path, e)
                continue

            # Game links go to maine.gov/tools/whatsnew with topic=Lottery_Scratch
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "maine.gov/tools/whatsnew" not in href:
                    continue
                if href in seen:
                    continue
                seen.add(href)

                name = a.get_text(strip=True)
                if not name or len(name) < 2:
                    continue

                game_id_m = re.search(r"id=(\d+)", href)
                game_id = game_id_m.group(1) if game_id_m else re.sub(r"[^a-z0-9]", "", name.lower())[:20]

                try:
                    overall_odds, total_tickets = self._scrape_detail(href)
                except Exception as e:
                    logger.debug("ME detail error for %s: %s", name, e)
                    overall_odds, total_tickets = None, None

                # ME only publishes overall odds + total tickets, no per-tier prize data.
                # Cannot compute real EV — pass empty tiers so ev=None is stored.
                game = self.build_game(
                    game_id=str(game_id),
                    name=name,
                    price=float(price),
                    tiers=[],
                    total_tickets=total_tickets,
                    overall_odds=overall_odds,
                    detail_url=href,
                )
                games.append(game)

        logger.info("ME: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> tuple[float | None, int | None]:
        """Returns (overall_odds, total_tickets) from maine.gov game page."""
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        overall_odds = None
        m = re.search(r"OVERALL\s+ODDS\s+(?:OF\s+WINNING\s+)?1[:\s]+([\d.,]+)", page_text, re.IGNORECASE)
        if m:
            overall_odds = float(m.group(1).replace(",", ""))

        total_tickets = None
        m2 = re.search(r"Tickets?\s+Printed\s+([\d,]+)", page_text, re.IGNORECASE)
        if m2:
            total_tickets = int(m2.group(1).replace(",", ""))

        return overall_odds, total_tickets
