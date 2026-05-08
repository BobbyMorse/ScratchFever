"""
Wisconsin Lottery scratch-off scraper.
Listing: https://www.wilottery.com/games/instant-games/scratch-games
  div.instant-listing-item[data-type=scratch][data-endi=9999999999999] = active games
  data-price, data-startd, div.card[title] are all in the listing HTML.
  Detail pages have Overall Odds but no per-tier prize table → no EV.
"""
import re
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

LIST_URL = "https://www.wilottery.com/games/instant-games/scratch-games"
BASE_URL = "https://www.wilottery.com"

_ACTIVE_ENDI = "9999999999999"


class WisconsinScraper(BaseScraper):
    state_code = "WI"
    state_name = "Wisconsin"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(LIST_URL)
        games = []
        seen = set()

        for div in soup.find_all("div", class_="instant-listing-item"):
            if div.get("data-type") != "scratch":
                continue
            if div.get("data-endi") != _ACTIVE_ENDI:
                continue  # expired or not yet released

            a = div.find("a", href=re.compile(r"/games/instant-games/[a-z0-9-]+-\d+"))
            if not a:
                continue
            href = a["href"]
            slug = href.rstrip("/").split("/")[-1]
            if slug in seen:
                continue
            seen.add(slug)

            # Name: from div.card[title] or h3
            card = a.find("div", class_="card")
            if card and card.get("title"):
                name = re.sub(r"\s+Scratch\s+Game\s*$", "", card["title"], flags=re.I).strip()
            else:
                h3 = div.find("h3")
                name = h3.get_text(strip=True) if h3 else ""
            if not name:
                name = re.sub(r"-\d+$", "", slug).replace("-", " ").title()

            try:
                price = float(div.get("data-price") or 0)
            except (ValueError, TypeError):
                price = None
            if not price:
                continue

            game_id_m = re.search(r"-(\d+)$", slug)
            game_id = game_id_m.group(1) if game_id_m else slug
            detail_url = (BASE_URL + href) if href.startswith("/") else href

            overall_odds = self._get_odds(detail_url)

            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=[],
                overall_odds=overall_odds,
                detail_url=detail_url,
            ))

        logger.info("WI: %d active games scraped", len(games))
        return games

    def _get_odds(self, url: str) -> float | None:
        try:
            soup = self.soup(url)
            text = soup.get_text(" ", strip=True)
            om = re.search(r"overall\s+odds\s+1[:\s]+(?:in\s+)?([\d.]+)", text, re.I)
            if om:
                return float(om.group(1))
        except Exception:
            pass
        return None
