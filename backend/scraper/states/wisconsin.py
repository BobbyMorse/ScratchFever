"""
Wisconsin Lottery scratch-off scraper.
Listing: https://www.wilottery.com/games/instant-games/scratch-games
  div.instant-listing-item[data-type=scratch] with future data-endi = active games
  WI publishes overall odds only (no per-tier prize table) → ev=NULL by design.
"""
from __future__ import annotations
import re
import time
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

LIST_URL = "https://www.wilottery.com/games/instant-games/scratch-games"
BASE_URL = "https://www.wilottery.com"


class WisconsinScraper(BaseScraper):
    state_code = "WI"
    state_name = "Wisconsin"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(LIST_URL)
        now_ms = int(time.time() * 1000)
        games = []
        seen = set()

        for div in soup.find_all("div", class_="instant-listing-item"):
            if div.get("data-type") != "scratch":
                continue
            try:
                if int(div.get("data-endi", 0)) < now_ms:
                    continue  # expired
            except (ValueError, TypeError):
                continue

            a = div.find("a", href=re.compile(r"/games/instant-games/[a-z0-9-]+-\d+"))
            if not a:
                continue
            href = a["href"]
            slug = href.rstrip("/").split("/")[-1]
            if slug in seen:
                continue
            seen.add(slug)

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

            img = div.find("img")
            image_url = None
            if img:
                src = img.get("data-src") or img.get("src") or ""
                if src:
                    image_url = (BASE_URL + src) if src.startswith("/") else src

            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=[],
                detail_url=detail_url,
                image_url=image_url,
            ))

        logger.info("WI: %d active games scraped", len(games))
        return games
