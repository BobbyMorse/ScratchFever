"""
Montana Lottery scratch-off scraper.
Listing: https://montanalottery.com/scratch-games/
WordPress/Elementor page — game data is inline:
  h2 → "$PRICE NAME" (e.g. "$10 Can-Am®")
  h2 → "Overall Odds 1:X.XX" (sibling in same container, 3 levels up)
  table → WIN | PRIZE | ODDS columns (prize=col1, odds=col2)
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://montanalottery.com/scratch-games/"
BASE_URL = "https://montanalottery.com"
MAX_PAGES = 20


class MontanaScraper(BaseScraper):
    state_code = "MT"
    state_name = "Montana"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        games = []
        seen = set()

        for page in range(1, MAX_PAGES + 1):
            url = GAMES_URL if page == 1 else f"{GAMES_URL}page/{page}/"
            try:
                soup = self.soup(url)
            except Exception:
                break

            page_games_before = len(games)
            self._parse_page(soup, games, seen)
            if len(games) == page_games_before:
                break

        logger.info("MT: %d games scraped", len(games))
        return games

    def _parse_page(self, soup, games: list, seen: set) -> None:

        for h2 in soup.find_all("h2"):
            try:
                text = h2.get_text(strip=True)
                price_m = re.match(r"\$(\d+)\s+(.+)", text)
                if not price_m:
                    continue
                price = float(price_m.group(1))
                name = price_m.group(2).strip()
                if not name or name in seen:
                    continue
                seen.add(name)

                # Game container is 3 parent levels up from the h2
                container = h2
                for _ in range(3):
                    if container.parent:
                        container = container.parent

                # Overall odds from sibling h2: "Overall Odds 1:X.XX"
                overall_odds = None
                for el in container.find_all("h2"):
                    el_text = el.get_text(strip=True)
                    om = re.search(r"overall\s+odds\s*1[:\s]+([\d.]+)", el_text, re.I)
                    if om:
                        overall_odds = float(om.group(1))
                        break

                # Prize table: WIN(0) | PRIZE(1) | ODDS(2)
                tiers = []
                table = container.find("table")
                if table:
                    rows = table.find_all("tr")
                    prize_col = odds_col = None
                    if rows:
                        hdrs = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
                        for i, h in enumerate(hdrs):
                            if h in ("prize", "amount", "win prize"):
                                prize_col = i
                            elif h in ("odds", "odd"):
                                odds_col = i
                        if prize_col is None:
                            prize_col = 1
                        if odds_col is None:
                            odds_col = 2

                    for row in rows[1:]:
                        cells = row.find_all(["td", "th"])
                        if len(cells) <= max(prize_col, odds_col):
                            continue
                        prize = parse_prize_amount(cells[prize_col].get_text(strip=True))
                        odds = parse_odds(cells[odds_col].get_text(strip=True).replace("1:", ""))
                        if prize and prize > 0 and odds and odds > 0:
                            tiers.append({
                                "prize_amount": prize,
                                "odds_one_in": odds,
                                "prizes_remaining": None,
                                "prizes_total": None,
                            })

                if not tiers and not overall_odds:
                    continue

                game_id = re.sub(r"[^a-z0-9]", "", name.lower())[:20]
                games.append(self.build_game(
                    game_id=game_id,
                    name=name,
                    price=price,
                    tiers=tiers,
                    overall_odds=overall_odds,
                ))
            except Exception as e:
                logger.debug("MT parse error: %s", e)
