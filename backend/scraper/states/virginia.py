"""
Virginia Lottery scratch-off scraper.
Listing: https://www.valottery.com/games/scratch-its (JS-rendered)
  Game cards with links to detail pages.
Detail:  https://www.valottery.com/games/scratch-its/{slug}
  Prize table with prize amounts, odds, and remaining counts.
"""
import re
import logging
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URLS = [
    "https://www.valottery.com/games/scratch-its",
    "https://www.valottery.com/games/scratch-offs",
    "https://www.valottery.com/games/instant-games",
]
BASE_URL = "https://www.valottery.com"


class VirginiaScraper(PlaywrightScraper):
    state_code = "VA"
    state_name = "Virginia"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = None
        working_url = None
        for url in GAMES_URLS:
            try:
                s = self.pw_soup(url, timeout=30_000)
                if s.select("a[href]"):
                    soup = s
                    working_url = url
                    break
            except Exception:
                continue

        if not soup:
            return []

        games = []
        seen = set()

        path_segment = working_url.rstrip("/").split("/")[-1]

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if f"/{path_segment}/" not in href and "/scratch" not in href.lower():
                continue
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug in seen or slug.lower() in (path_segment, "games", ""):
                continue
            seen.add(slug)

            name_el = a.select_one("h3, h4, .game-name, [class*='title'], [class*='name']")
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                name = slug.replace("-", " ").title()

            price_el = a.select_one("[class*='price'], [class*='cost']")
            price = parse_prize_amount(price_el.get_text(strip=True)) if price_el else None
            if not price:
                pm = re.search(r"\$(\d+)", a.get_text())
                price = float(pm.group(1)) if pm else None

            detail_url = (BASE_URL + href) if href.startswith("/") else href

            tiers, detail_price = [], None
            try:
                tiers, detail_price = self._scrape_detail(detail_url)
            except Exception as e:
                logger.debug("VA detail failed for %s: %s", slug, e)

            if not price:
                price = detail_price
            if not price:
                continue

            game_id = re.sub(r"[^a-z0-9]", "", slug.lower())[:30] or slug

            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=tiers,
                detail_url=detail_url,
            ))

        logger.info("VA: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> tuple[list, float | None]:
        soup = self.pw_soup(url, timeout=20_000)
        page_text = soup.get_text(" ", strip=True)

        price = None
        pm = re.search(r"(?:Ticket\s+)?Price[:\s]+\$?([\d.]+)", page_text, re.I)
        if pm:
            price = float(pm.group(1))
        if not price:
            for el in soup.select("[class*='price'], .ticket-price"):
                p = parse_prize_amount(el.get_text(strip=True))
                if p:
                    price = p
                    break

        tiers = []
        for table in soup.find_all("table"):
            text = table.get_text().lower()
            if any(k in text for k in ("prize", "odds", "1 in", "winning")):
                tiers = self.parse_table_tiers(table)
                if tiers:
                    break

        return tiers, price
