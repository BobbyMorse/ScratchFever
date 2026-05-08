"""
Idaho Lottery scratch-off scraper.
Listing pages per price: /games/scratch/1, /games/scratch/2, /games/scratch/5, etc.
Each game entry has game title and "/games/scratch/slug" link.
Detail page: "Overall Odds: 1:X.XX", table with Prize Amount | Remaining Prizes | Odds.
Odds format "1:X" in detail table.
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

BASE_URL = "https://www.idaholottery.com"
PRICE_PAGES = [
    (1,  "/index.php/games/scratch/1"),
    (2,  "/index.php/games/scratch/2"),
    (3,  "/index.php/games/scratch/3"),
    (5,  "/index.php/games/scratch/5"),
    (10, "/index.php/games/scratch/10"),
    (20, "/index.php/games/scratch/20"),
    (25, "/index.php/games/scratch/25"),
    (30, "/index.php/games/scratch/30"),
]


class IdahoScraper(BaseScraper):
    state_code = "ID"
    state_name = "Idaho"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        games = []
        seen = set()

        for price, path in PRICE_PAGES:
            url = BASE_URL + path
            try:
                soup = self.soup(url)
            except Exception as e:
                logger.debug("ID listing error for %s: %s", path, e)
                continue

            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not re.search(r"/games/scratch/[a-z0-9-]+", href):
                    continue
                slug = href.rstrip("/").split("/")[-1]
                if not slug or slug in seen or re.match(r"^\d+$", slug):
                    continue

                name_el = a.find(["h2", "h3", "h4", "span"])
                name = name_el.get_text(strip=True) if name_el else a.get_text(strip=True)
                if not name:
                    continue
                seen.add(slug)

                detail_url = (BASE_URL + href) if href.startswith("/") else href
                tiers, overall_odds = [], None
                try:
                    tiers, overall_odds = self._scrape_detail(detail_url)
                except Exception as e:
                    logger.debug("ID detail failed for %s: %s", name, e)

                games.append(self.build_game(
                    game_id=slug,
                    name=name,
                    price=float(price),
                    tiers=tiers,
                    overall_odds=overall_odds,
                    detail_url=detail_url,
                ))

        logger.info("ID: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> tuple[list, float | None]:
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        overall_odds = None
        om = re.search(r"overall\s+odds[:\s]+1[:\s]+([\d.]+)", page_text, re.I)
        if om:
            overall_odds = float(om.group(1))

        tiers = []
        for table in soup.find_all("table"):
            text = table.get_text().lower()
            if any(k in text for k in ("prize", "odds", "remaining")):
                rows = table.find_all("tr")
                if len(rows) < 2:
                    continue
                hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
                prize_col = odds_col = rem_col = None
                for i, h in enumerate(hdrs):
                    if "remaining" in h:
                        rem_col = i
                    elif "prize" in h or "amount" in h:
                        prize_col = i
                    elif "odd" in h:
                        odds_col = i
                if prize_col is None:
                    prize_col = 0
                if odds_col is None:
                    odds_col = 2
                for row in rows[1:]:
                    cells = row.find_all(["td", "th"])
                    if len(cells) <= max(prize_col, odds_col):
                        continue
                    prize = parse_prize_amount(cells[prize_col].get_text(strip=True))
                    odds_txt = cells[odds_col].get_text(strip=True).replace("1:", "")
                    odds = parse_odds(odds_txt)
                    if not prize or prize <= 0:
                        continue
                    rem = None
                    if rem_col is not None and len(cells) > rem_col:
                        try:
                            rem = int(cells[rem_col].get_text(strip=True).replace(",", ""))
                        except (ValueError, TypeError):
                            pass
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds,
                        "prizes_remaining": rem,
                        "prizes_total": None,
                    })
                if tiers:
                    break

        return tiers, overall_odds
