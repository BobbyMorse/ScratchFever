"""
Missouri Lottery scratch-off scraper.
Listing page: /scratchers-list.do — server-rendered HTML with game links ?method=d&game=<num>.
Detail page: /scratchers.do?method=d&game=<num> — prize table has:
  Prize Level | Total Prizes | Unclaimed Prizes
No per-tier odds published; tickets_remaining derived from overall_odds × sum(unclaimed prizes).
"""
from __future__ import annotations
import re
import logging
from datetime import datetime, date
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://www.molottery.com"
LISTING_URL = f"{BASE_URL}/scratchers-list.do"


class MissouriScraper(BaseScraper):
    state_code = "MO"
    state_name = "Missouri"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        game_nums = self._get_game_numbers()
        games = []
        for num in game_nums:
            url = f"{BASE_URL}/scratchers.do?method=d&game={num}"
            try:
                game = self._scrape_detail(num, url)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("MO detail failed for game %s: %s", num, e)
        logger.info("MO: %d games scraped", len(games))
        return games

    def _get_game_numbers(self) -> list[str]:
        soup = self.soup(LISTING_URL)
        seen: set[str] = set()
        nums: list[str] = []
        for a in soup.find_all("a", href=True):
            m = re.search(r"[?&]game=(\d+)", a["href"])
            if not m:
                continue
            num = m.group(1)
            if num not in seen:
                seen.add(num)
                nums.append(num)
        return nums

    def _scrape_detail(self, game_num: str, url: str) -> dict | None:
        soup = self.soup(url)
        text = soup.get_text(" ", strip=True)

        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else f"MO Game {game_num}"

        price = None
        pm = re.search(r"[Tt]icket\s+[Pp]rice[:\s]+\$([\d.]+)", text)
        if pm:
            price = float(pm.group(1))
        if not price:
            return None

        end_date = None
        edm = re.search(r"End\s+Date[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})", text)
        if edm:
            try:
                parsed = datetime.strptime(edm.group(1).strip(), "%b %d, %Y").date()
                if parsed < date.today():
                    logger.debug("MO: skipping expired game %s (end %s)", game_num, parsed)
                    return None
                end_date = parsed.isoformat()
            except ValueError:
                pass

        overall_odds = None
        om = re.search(r"[Aa]verage\s+[Cc]hances[*:.\s]+1\s+in\s+([\d,]+(?:\.\d+)?)", text)
        if om:
            overall_odds = float(om.group(1).replace(",", ""))

        tiers = []
        for table in soup.find_all("table"):
            t = self.parse_table_tiers(table)
            if t:
                tiers = t
                break

        if not tiers:
            return None

        total_tickets = None
        tickets_remaining = None
        if overall_odds:
            sum_total = sum(t["prizes_total"] for t in tiers if t.get("prizes_total"))
            sum_remaining = sum(
                t["prizes_remaining"] for t in tiers if t.get("prizes_remaining") is not None
            )
            if sum_total:
                total_tickets = round(overall_odds * sum_total)
            if sum_remaining:
                tickets_remaining = round(overall_odds * sum_remaining)

        return self.build_game(
            game_id=f"mo{game_num}",
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=url,
            image_url=f"{BASE_URL}/media/scratchers/tile/{game_num}.png",
        )
