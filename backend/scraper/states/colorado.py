"""
Colorado Lottery scratch-off scraper.
List page: https://www.coloradolottery.com/en/games/scratch/
  - Contains 90+ links like /en/games/scratch/game/{slug}/
Game page: https://www.coloradolottery.com/en/games/scratch/game/{slug}/
  - Price in .price element
  - Prize table with columns: Prize Amount | Winning Tickets | Probability
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

LIST_URL = "https://www.coloradolottery.com/en/games/scratch/"
BASE_URL = "https://www.coloradolottery.com"


class ColoradoScraper(BaseScraper):
    state_code = "CO"
    state_name = "Colorado"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(LIST_URL)
        games = []

        # Collect unique game URLs from links
        seen = set()
        game_urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/en/games/scratch/game/" in href and href not in seen:
                seen.add(href)
                full_url = BASE_URL + href if href.startswith("/") else href
                game_urls.append(full_url)

        logger.info("CO: found %d game URLs", len(game_urls))

        for url in game_urls:
            try:
                game = self._scrape_game(url)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("CO game scrape failed %s: %s", url, e)

        return games

    def _scrape_game(self, url: str) -> dict | None:
        soup = self.soup(url)

        # Game name from <h1> or <title>
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""
        if not name:
            title = soup.find("title")
            if title:
                name = title.get_text(strip=True).split("|")[0].strip()
        if not name:
            # Extract from URL slug
            m = re.search(r"/game/([^/]+)/?$", url)
            name = m.group(1).replace("-", " ").title() if m else "Unknown"
            # Strip trailing game number like "-2924"
            name = re.sub(r"\s+\d{4}$", "", name).strip()

        # Price from .price element
        price_el = soup.select_one("p.price, strong.price, .price")
        price = None
        if price_el:
            price = parse_prize_amount(price_el.get_text(strip=True))
        if not price:
            # Search in page text
            text = soup.get_text()
            m = re.search(r"\$(\d+)\s+(?:scratch|ticket|game)", text, re.I)
            if m:
                price = float(m.group(1))
        if not price:
            return None

        # Slug-based game ID
        m = re.search(r"/game/(.+?)/?$", url)
        game_id = m.group(1) if m else re.sub(r"[^a-z0-9]", "", name.lower())[:30]

        # Prize table: Prize Amount | Winning Tickets | Probability
        tiers = []
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not any("prize" in h or "probability" in h or "winning" in h for h in headers):
                continue

            col = {}
            for i, h in enumerate(headers):
                if "prize" in h and "amount" in h:
                    col["prize"] = i
                elif "winning" in h or "ticket" in h:
                    col["total"] = i  # "Winning Tickets" = total prizes printed
                elif "probability" in h or "odds" in h or "1 in" in h:
                    col["odds"] = i

            rows = table.find_all("tr")[1:]
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue

                prize_idx = col.get("prize", 0)
                odds_idx = col.get("odds", 2)
                tot_idx = col.get("total", 1)

                prize = parse_prize_amount(cells[prize_idx]) if prize_idx < len(cells) else None
                # Odds is in "1 in X,XXX" format
                odds = parse_odds(cells[odds_idx]) if odds_idx < len(cells) else None
                total = None
                if tot_idx < len(cells):
                    try:
                        total = int(cells[tot_idx].replace(",", ""))
                    except ValueError:
                        pass

                if prize and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds,
                        "prizes_remaining": None,  # CO does not publish remaining counts
                        "prizes_total": total,
                    })

            if tiers:
                break

        # Overall odds from page text
        text = soup.get_text()
        overall_odds = None
        m = re.search(r"overall odds.*?1\s*in\s*([\d,.]+)", text, re.I)
        if m:
            overall_odds = parse_odds(m.group(1))

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            detail_url=url,
        )
