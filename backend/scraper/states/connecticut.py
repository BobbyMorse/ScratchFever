"""
Connecticut Lottery scratch-off scraper.
Listing table: https://www.ctlottery.org/ScratchGamesTable
  Columns: Game | Game Name | Price | Top Prize | GameStart | TopPrizes | Top Prizes Unclaimed | Last day to redeem
Detail:  https://www.ctlottery.org/ScratchGames/{NUMBER}/
  Info table (key-value): Ticket Price, Overall Odds
  Prize table: Prize Amount | Total Prizes | Unclaimed Prizes
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

LIST_URL = "https://www.ctlottery.org/ScratchGamesTable"
BASE_URL = "https://www.ctlottery.org"


class ConnecticutScraper(BaseScraper):
    state_code = "CT"
    state_name = "Connecticut"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(LIST_URL)
        games = []
        seen = set()

        # Find the listing table (has column headers including "Game Name")
        listing_table = None
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if "game name" in " ".join(headers):
                listing_table = table
                break

        if not listing_table:
            logger.warning("CT: could not find listing table at %s", LIST_URL)
            return []

        # Parse header positions
        headers = [th.get_text(strip=True).lower() for th in listing_table.find_all("th")]
        col = {}
        for i, h in enumerate(headers):
            if h == "game":
                col["num"] = i
            elif "game name" in h:
                col["name"] = i
            elif h == "price":
                col["price"] = i
            elif "top prize" in h and "unclaimed" not in h:
                col["top_prize"] = i

        if "name" not in col:
            logger.warning("CT: listing table missing 'Game Name' column")
            return []

        for row in listing_table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if not cells or len(cells) < 3:
                continue

            # Game number from link or first column
            a = row.find("a", href=True)
            m = re.search(r"/ScratchGames/(\d+)/?", a["href"]) if a else None
            if not m:
                try:
                    game_num = cells[col.get("num", 0)]
                except IndexError:
                    continue
            else:
                game_num = m.group(1)

            if game_num in seen:
                continue
            seen.add(game_num)

            name = cells[col["name"]] if col.get("name") is not None and col["name"] < len(cells) else f"Game {game_num}"
            price_txt = cells[col["price"]] if col.get("price") is not None and col["price"] < len(cells) else ""
            price = parse_prize_amount(price_txt)
            if not price:
                continue

            detail_url = f"{BASE_URL}/ScratchGames/{game_num}/"
            try:
                tiers, overall_odds = self._scrape_detail(detail_url)
            except Exception as e:
                logger.debug("CT detail error for game %s: %s", game_num, e)
                tiers, overall_odds = [], None

            if not tiers and not overall_odds:
                continue

            # Derive total_tickets from overall_odds and total prizes
            total_prizes_printed = sum(t.get("prizes_total") or 0 for t in tiers)
            total_tickets = round(total_prizes_printed * overall_odds) if overall_odds and total_prizes_printed else None

            if total_tickets:
                for t in tiers:
                    if t.get("prizes_total"):
                        t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

            tickets_remaining = None
            has_remaining_data = any(t.get("prizes_remaining") is not None for t in tiers)
            if total_tickets and total_prizes_printed > 0 and has_remaining_data:
                unclaimed = sum(t.get("prizes_remaining") or 0 for t in tiers)
                tickets_remaining = round(total_tickets * unclaimed / total_prizes_printed) or None

            games.append(self.build_game(
                game_id=game_num,
                name=name,
                price=price,
                tiers=tiers,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                overall_odds=overall_odds,
                detail_url=detail_url,
                image_url=f"https://www.ctlottery.org/Content/images/Scratch/thumbnails/{game_num}.jpg",
            ))

        logger.info("CT: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> tuple[list, float | None]:
        """Returns (tiers, overall_odds)."""
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        overall_odds = None
        m = re.search(r"Overall\s+Odds[:\s]+1\s+in\s+([\d.]+)", page_text, re.IGNORECASE)
        if m:
            overall_odds = float(m.group(1))

        tiers = []
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            full = " ".join(headers)
            if not any(k in full for k in ("prize amount", "unclaimed", "total prizes")):
                continue

            col_prize = col_total = col_unclaimed = None
            for i, h in enumerate(headers):
                if "prize amount" in h:
                    col_prize = i
                elif "total" in h:
                    col_total = i
                elif "unclaimed" in h or "remaining" in h:
                    col_unclaimed = i

            if col_prize is None:
                continue

            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if not cells or len(cells) < 2:
                    continue

                prize = parse_prize_amount(cells[col_prize]) if col_prize < len(cells) else None
                total = unclaimed = None

                if col_total is not None and col_total < len(cells):
                    try:
                        total = int(cells[col_total].replace(",", ""))
                    except ValueError:
                        pass
                if col_unclaimed is not None and col_unclaimed < len(cells):
                    try:
                        unclaimed = int(cells[col_unclaimed].replace(",", ""))
                    except ValueError:
                        pass

                if prize and prize > 0:
                    tiers.append({
                        "prize_amount":     prize,
                        "odds_one_in":      None,
                        "prizes_total":     total,
                        "prizes_remaining": unclaimed,
                    })
            if tiers:
                break

        return tiers, overall_odds
