"""
South Carolina Education Lottery scratch-off scraper.
Listing: https://www.sceducationlottery.com/lottery/draw/Scratch.aspx
Games: <a href="/Games/InstantGame?gameId=NUMBER"> with img alt "Name Scratch-Off Game Link"
Detail page: "Ticket Price: $X", "Overall Odds: 1 in X.XX",
table with Prize Amount | Estimated Number of Unclaimed Prizes | Number of Prizes at Start.
EV computed from remaining prize data.
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.sceducationlottery.com/lottery/draw/Scratch.aspx"
BASE_URL = "https://www.sceducationlottery.com"


class SouthCarolinaScraper(BaseScraper):
    state_code = "SC"
    state_name = "South Carolina"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"[Gg]ame[Ii]d=(\d+)", href)
            if not m:
                continue
            game_id = m.group(1)
            if game_id in seen:
                continue
            seen.add(game_id)

            # Game name from img alt: "In the Green Scratch-Off Game Link" → strip suffix
            img = a.find("img")
            alt = img.get("alt", "").strip() if img else ""
            name = re.sub(r"\s+Scratch-?Off\s+Game\s+Link\s*$", "", alt, flags=re.I).strip()
            if not name:
                name = f"Game {game_id}"

            # Image URL from listing page img src
            image_url = None
            if img:
                src = img.get("src", "")
                if src:
                    image_url = (BASE_URL + src) if src.startswith("/") else src

            detail_url = (BASE_URL + href) if href.startswith("/") else href
            price, tiers, overall_odds, tickets_remaining, total_tickets = \
                None, [], None, None, None
            try:
                price, tiers, overall_odds, tickets_remaining, total_tickets = \
                    self._scrape_detail(detail_url)
            except Exception as e:
                logger.debug("SC detail failed for %s: %s", name, e)

            if not price:
                continue

            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=tiers,
                overall_odds=overall_odds,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                detail_url=detail_url,
                image_url=image_url,
            ))

        logger.info("SC: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str):
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        price = None
        pm = re.search(r"(?:ticket\s+)?price[:\s]+\$?([\d.]+)", page_text, re.I)
        if pm:
            price = float(pm.group(1))

        overall_odds = None
        om = re.search(r"overall\s+odds[:\s]+1\s+in\s+([\d.]+)", page_text, re.I)
        if om:
            overall_odds = float(om.group(1))

        # Table: Prize Amount | Unclaimed Prizes | Unclaimed Value | Prizes at Start | ...
        tiers = []
        total_prizes = 0
        remaining_prizes = 0
        for table in soup.find_all("table"):
            hdrs = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not any("prize" in h or "amount" in h for h in hdrs):
                continue

            prize_col = rem_col = total_col = None
            for i, h in enumerate(hdrs):
                if prize_col is None and ("prize amount" in h or "amount" in h or h.startswith("prize")):
                    prize_col = i
                elif rem_col is None and "unclaimed" in h and "number" in h:
                    rem_col = i
                elif total_col is None and "start" in h and "number" in h:
                    total_col = i

            if prize_col is None:
                prize_col = 0
            if rem_col is None:
                rem_col = 1
            if total_col is None:
                total_col = 3

            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                max_col = max(prize_col, rem_col, total_col)
                if len(cells) <= max_col:
                    continue
                prize = parse_prize_amount(cells[prize_col].get_text(strip=True))
                if not prize or prize <= 0:
                    continue
                try:
                    rem = int(cells[rem_col].get_text(strip=True).replace(",", ""))
                except (ValueError, TypeError):
                    rem = None
                try:
                    orig = int(cells[total_col].get_text(strip=True).replace(",", ""))
                except (ValueError, TypeError):
                    orig = None

                if orig:
                    total_prizes += orig
                if rem is not None:
                    remaining_prizes += rem

                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": None,
                    "prizes_remaining": rem,
                    "prizes_total": orig,
                })
            if tiers:
                break

        # Estimate ticket counts
        tickets_remaining = total_tickets = None
        if overall_odds and total_prizes > 0:
            total_tickets = round(total_prizes * overall_odds)
            if remaining_prizes > 0:
                tickets_remaining = round(total_tickets * remaining_prizes / total_prizes)

        return price, tiers, overall_odds, tickets_remaining, total_tickets
