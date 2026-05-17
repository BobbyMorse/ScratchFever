"""
Nebraska Lottery scratch-off scraper.
Listing: https://nelottery.com/homeapp/scratch/gamessummary
Page has <h3>$X Scratch Games</h3> headers followed by
<a href="https://nelottery.com/homeapp/scratch/ID/0/gamedetail/web"> links.
Detail page: 4-col table (Odds | Prize | Odds | Winners) with decimal odds,
and "Overall odds 1 in X.XX" in page text.
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://nelottery.com/homeapp/scratch/gamessummary"
BASE_URL = "https://nelottery.com"


class NebraskaScraper(BaseScraper):
    state_code = "NE"
    state_name = "Nebraska"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()
        current_price = None

        for tag in soup.find_all(["h3", "a"]):
            if tag.name == "h3":
                m = re.match(r"\$(\d+)\s+scratch\s+games?", tag.get_text(strip=True), re.I)
                if m:
                    current_price = float(m.group(1))
            elif tag.name == "a" and current_price:
                href = tag.get("href", "")
                m = re.search(r"/homeapp/scratch/(\d+)/", href)
                if not m:
                    continue
                game_id = m.group(1)
                if game_id in seen:
                    continue
                seen.add(game_id)

                # name is text content after stripping the img element
                img = tag.find("img")
                image_url = None
                if img:
                    src = img.get("src") or img.get("data-src", "")
                    if src:
                        image_url = (BASE_URL + src) if src.startswith("/") else src
                    img.extract()
                name = tag.get_text(strip=True)
                if not name:
                    continue

                detail_url = href if href.startswith("http") else BASE_URL + href
                tiers, overall_odds = [], None
                try:
                    tiers, overall_odds = self._scrape_detail(detail_url)
                except Exception as e:
                    logger.debug("NE detail failed for %s: %s", name, e)

                games.append(self.build_game(
                    game_id=game_id,
                    name=name,
                    price=current_price,
                    tiers=tiers,
                    overall_odds=overall_odds,
                    detail_url=detail_url,
                    image_url=image_url,
                ))

        logger.info("NE: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> tuple[list, float | None]:
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        overall_odds = None
        om = re.search(r"overall\s+odds\s*1\s+in\s+([\d.]+)", page_text, re.I)
        if om:
            overall_odds = float(om.group(1))

        tiers = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            # NE table layout: Odds(0) | Prize(1) | Odds(2) | Winners(3)
            # Use fixed col indices to avoid duplicate-header confusion
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                odds = parse_odds(cells[0].get_text(strip=True))
                prize = parse_prize_amount(cells[1].get_text(strip=True))
                if prize and prize > 0 and odds and odds > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds,
                        "prizes_remaining": None,
                        "prizes_total": None,
                    })
            if tiers:
                break

        return tiers, overall_odds
