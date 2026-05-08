"""
Ohio Lottery scratch-off scraper.
Listing: https://www.ohiolottery.com/games/scratch-offs (JS-rendered, ~210 games)
  Links: /games/scratch-offs/$X-games/game-slug  (price encoded in URL)
Detail:  https://www.ohiolottery.com/games/scratch-offs/$X-games/game-slug
  Prize table on detail page (generic HTML table with prize/odds columns)
"""
import re
import logging
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.ohiolottery.com/games/scratch-offs"
BASE_URL = "https://www.ohiolottery.com"

_PRICE_RE = re.compile(r"/\$(\d+)-games/", re.I)
_SLUG_RE = re.compile(r"/games/scratch-offs/\$\d+-games/([^/?#]+)", re.I)


class OhioScraper(PlaywrightScraper):
    state_code = "OH"
    state_name = "Ohio"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.pw_soup(GAMES_URL, timeout=45_000)
        games = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            price_m = _PRICE_RE.search(href)
            slug_m = _SLUG_RE.search(href)
            if not price_m or not slug_m:
                continue
            slug = slug_m.group(1)
            if slug in seen:
                continue
            seen.add(slug)

            price = float(price_m.group(1))
            detail_url = (BASE_URL + href) if href.startswith("/") else href

            name_el = a.select_one("h3, h4, .game-name, .name, [class*='title']")
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                name = slug.replace("-", " ").title()
                name = re.sub(r"\s*\d+$", "", name).strip()

            tiers = []
            try:
                tiers = self._scrape_detail(detail_url)
            except Exception as e:
                logger.debug("OH detail failed for %s: %s", slug, e)

            games.append(self.build_game(
                game_id=slug,
                name=name,
                price=price,
                tiers=tiers,
                detail_url=detail_url,
            ))

        logger.info("OH: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> list[dict]:
        soup = self.pw_soup(url, timeout=20_000)
        for table in soup.find_all("table"):
            text = table.get_text().lower()
            if any(k in text for k in ("prize", "odds", "1 in", "winning")):
                tiers = self.parse_table_tiers(table)
                if tiers:
                    return tiers
        return []
