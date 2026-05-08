"""
Pennsylvania Lottery scratch-off scraper.
Source: https://www.palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx
  Single page with all active games: Game# | Name | Price | Top Six Prizes | Wins Remaining
  Prize table only shows top 6 prize tiers — incomplete for EV calculation.
  Games are stored with name/price/top prizes but ev=None.
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

LIST_URL = "https://www.palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx"
BASE_URL = "https://www.palottery.pa.gov"


class PennsylvaniaScraper(BaseScraper):
    state_code = "PA"
    state_name = "Pennsylvania"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(LIST_URL)

        table = None
        for t in soup.find_all("table"):
            text = t.get_text().lower()
            if "game" in text and "prize" in text and "remaining" in text:
                table = t
                break
        if not table:
            logger.warning("PA: prizes remaining table not found")
            return []

        rows = table.find_all("tr")
        games = []
        seen = set()

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 5:
                continue

            # Cell 0: game ID in data-order or span
            game_id_raw = cells[0].get("data-order") or cells[0].get_text(strip=True)
            game_id = re.sub(r"[^\d]", "", str(game_id_raw)) or game_id_raw
            if not game_id or game_id in seen:
                continue
            seen.add(game_id)

            # Cell 1: name and detail URL
            a = cells[1].find("a")
            name = a.get_text(strip=True) if a else cells[1].get_text(strip=True)
            name = re.sub(r"\s*\(PA[–\-‑]\d+\)\s*$", "", name).strip()
            href = a["href"] if a else None
            detail_url = (BASE_URL + href) if href and href.startswith("/") else href

            # Cell 2: price "$X" or just "X"
            price_text = cells[2].get("data-order") or cells[2].get_text(strip=True)
            price = parse_prize_amount(str(price_text))
            if not price:
                continue

            # Cells 3+4: prize divs and wins remaining divs
            prize_divs = cells[3].find_all("div")
            rem_divs = cells[4].find_all("div")

            tiers = []
            for pd, rd in zip(prize_divs, rem_divs):
                prize = parse_prize_amount(pd.get_text(strip=True))
                try:
                    remaining = int(rd.get_text(strip=True).replace(",", ""))
                except ValueError:
                    remaining = None
                if prize and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "prizes_remaining": remaining,
                        "prizes_total": None,
                        "odds_one_in": None,
                    })

            # No tickets_remaining passed — EV stays None (only top-6 prizes visible)
            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=tiers,
                detail_url=detail_url,
            ))

        logger.info("PA: %d games scraped", len(games))
        return games
