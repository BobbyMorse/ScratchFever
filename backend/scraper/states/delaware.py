"""
Delaware Lottery scratch-off scraper.
Listing: https://www.delottery.com/Instant-Games
Game cards display "Name - $X" format.
Delaware does not publish per-game odds tables publicly,
so EV will be NULL (games won't appear in the dropdown until odds are available).
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.delottery.com/Instant-Games"
BASE_URL = "https://www.delottery.com"


class DelawareScraper(BaseScraper):
    state_code = "DE"
    state_name = "Delaware"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        # Look for any element whose text matches "Name - $X" pattern
        for el in soup.find_all(["h3", "h4", "h2", "span", "div", "p", "td"]):
            text = el.get_text(strip=True)
            m = re.match(r"^(.*?)\s*[-–]\s*\*{0,2}\$(\d+)\*{0,2}$", text)
            if not m:
                continue
            name = m.group(1).strip()
            price = float(m.group(2))
            if not name or not price or name in seen:
                continue
            # Skip navigation/filter labels that aren't game names
            if len(name) < 2 or re.match(r"^\$?\d+$", name):
                continue
            seen.add(name)

            game_id = re.sub(r"[^a-z0-9]", "", name.lower())[:20]
            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=[],
            ))

        logger.info("DE: %d games scraped", len(games))
        return games
