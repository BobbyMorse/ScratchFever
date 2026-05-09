"""
Illinois Lottery scratch-off scraper.
Listing: https://www.illinoislottery.com/games-hub/instant-tickets (JS-rendered)
  Game cards with links to /games-hub/instant-tickets/{slug}
  Price extracted from card text: "$ X / ticket"
  Note: Cloudflare Turnstile blocks detail pages; prize table data not accessible.
  Games are persisted with name + price but no EV.
"""
import re
import logging
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.illinoislottery.com/games-hub/instant-tickets"
BASE_URL = "https://www.illinoislottery.com"


class IllinoisScraper(PlaywrightScraper):
    state_code = "IL"
    state_name = "Illinois"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        soup = self.pw_soup(GAMES_URL, wait_for="load", timeout=45_000)
        games = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/games-hub/instant-tickets/" not in href:
                continue
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug in seen:
                continue
            seen.add(slug)

            # Walk up to find card container with price text
            parent = a
            price = None
            name = ""
            for _ in range(6):
                if parent is None:
                    break
                text = parent.get_text(" ", strip=True)
                if "/ ticket" in text or "/ticket" in text:
                    pm = re.search(r"\$\s*([\d.]+)\s*/\s*ticket", text, re.I)
                    if pm:
                        price = float(pm.group(1))
                    # Try image alt for name
                    img = parent.find("img")
                    if img and img.get("alt"):
                        name = img["alt"].strip()
                    break
                parent = parent.parent

            if not name:
                name = slug.replace("-", " ").title()
            if not price:
                continue

            game_id = re.sub(r"[^a-z0-9]", "", slug.lower())[:30] or slug
            detail_url = BASE_URL + "/" + href.lstrip("/")

            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=[],
                detail_url=detail_url,
            ))

        logger.info("IL: %d games scraped", len(games))
        return games
