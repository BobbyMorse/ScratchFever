"""
Indiana Hoosier Lottery scratch-off scraper.
Listing: https://hoosierlottery.com/games/scratch-off/
  <a class="game" data-id, data-name, data-price href="/games/scratch-off/[slug]">
Detail: https://hoosierlottery.com/games/scratch-off/[slug]
  table.prize-table: Prize Amount | Unclaimed | Total Winning Tickets
  [class*=odds]: "Overall Odds: 1 in X.XX"
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

GAMES_URL = "https://hoosierlottery.com/games/scratch-off/"
BASE_URL = "https://hoosierlottery.com"


class IndianaScraper(BaseScraper):
    state_code = "IN"
    state_name = "Indiana"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        for a in soup.find_all("a", attrs={"data-id": True}):
            href = a.get("href", "")
            if "/games/scratch-off/" not in href:
                continue
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug in seen:
                continue
            seen.add(slug)

            name = a.get("data-name", "").strip().title()
            if not name:
                continue

            try:
                price = float(a.get("data-price") or 0)
            except (ValueError, TypeError):
                price = None
            if not price:
                continue

            detail_url = (BASE_URL + href) if href.startswith("/") else href
            overall_odds = self._get_overall_odds(detail_url)

            img = a.find("img")
            image_url = img["src"] if img and img.get("src") else None

            # IN prize table is explicitly incomplete (only prizes >= $40); skip tiers to avoid bogus EV
            games.append(self.build_game(
                game_id=str(a.get("data-id") or slug),
                name=name,
                price=price,
                tiers=[],
                overall_odds=overall_odds,
                detail_url=detail_url,
                image_url=image_url,
            ))

        logger.info("IN: %d games scraped", len(games))
        return games

    def _get_overall_odds(self, url: str) -> float | None:
        try:
            soup = self.soup(url)
            # "Estimated Overall Odds: 1 in X.XX" in detail page text
            for el in soup.select("[class*=odds]"):
                m = re.search(r"overall\s+odds[:\s]+1\s+in\s+([\d.]+)", el.get_text(strip=True), re.I)
                if m:
                    return float(m.group(1))
        except Exception:
            pass
        return None
