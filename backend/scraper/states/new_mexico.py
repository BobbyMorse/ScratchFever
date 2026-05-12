"""
New Mexico Lottery scratch-off scraper.
Listing: https://www.nmlottery.com/games/scratchers/
Server-rendered WordPress page. Each game is a section with:
  <h3>Name</h3> and <p>$X</p> for price (in the same parent div),
  inline table: Prize(0) | Approx. Odds 1 in(1) | Approx. # Prizes(2) | Prizes Remaining(3)
  <p>Approximate overall odds...1 in X.XX</p>
Game ID from img src filename (e.g. 682.jpg → "682").
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.nmlottery.com/games/scratchers/"
BASE_URL = "https://www.nmlottery.com"


class NewMexicoScraper(BaseScraper):
    state_code = "NM"
    state_name = "New Mexico"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        for h3 in soup.find_all("h3"):
            try:
                name = h3.get_text(strip=True)
                if not name or name in seen:
                    continue

                container = h3.parent

                # Price from <p>$X</p> (only the ticket price — exact pattern "$N")
                price = None
                for p in container.find_all("p"):
                    m = re.match(r"^\$(\d+)$", p.get_text(strip=True))
                    if m:
                        price = float(m.group(1))
                        break
                if not price:
                    continue

                seen.add(name)

                # Overall odds
                overall_odds = None
                for p in container.find_all("p"):
                    om = re.search(r"overall\s+odds.*?1\s+in\s+([\d.]+)", p.get_text(strip=True), re.I)
                    if om:
                        overall_odds = float(om.group(1))
                        break

                # Game ID and image URL from img src filename
                img = container.find("img")
                game_id = None
                image_url = None
                if img:
                    src = img.get("src", "")
                    m = re.search(r"/(\d+)\.(?:jpg|png|webp)", src, re.I)
                    game_id = m.group(1) if m else None
                    if src:
                        image_url = (BASE_URL + src) if src.startswith("/") else src
                if not game_id:
                    game_id = re.sub(r"[^a-z0-9]", "", name.lower())[:20]

                # Prize table: Prize(0) | Odds(1) | Total(2) | Remaining(3)
                tiers = []
                table = container.find("table")
                if table:
                    rows = table.find_all("tr")
                    for row in rows[1:]:
                        cells = row.find_all(["td", "th"])
                        if len(cells) < 2:
                            continue
                        prize = parse_prize_amount(cells[0].get_text(strip=True))
                        odds = parse_odds(cells[1].get_text(strip=True))
                        if not prize or prize <= 0:
                            continue
                        total = rem = None
                        if len(cells) > 2:
                            try:
                                total = int(cells[2].get_text(strip=True).replace(",", ""))
                            except (ValueError, TypeError):
                                pass
                        if len(cells) > 3:
                            try:
                                rem = int(cells[3].get_text(strip=True).replace(",", ""))
                            except (ValueError, TypeError):
                                pass
                        tiers.append({
                            "prize_amount": prize,
                            "odds_one_in": odds,
                            "prizes_remaining": rem,
                            "prizes_total": total,
                        })

                if not tiers and not overall_odds:
                    continue

                games.append(self.build_game(
                    game_id=str(game_id),
                    name=name,
                    price=price,
                    tiers=tiers,
                    overall_odds=overall_odds,
                    image_url=image_url,
                ))
            except Exception as e:
                logger.debug("NM parse error: %s", e)

        logger.info("NM: %d games scraped", len(games))
        return games
