"""
Arkansas Scholarship Lottery scratch-off scraper.
Listing: https://myarkansaslottery.com/games/instant
Each game: <a href="/games/slug"> containing only <img alt="Name - Game No. XXX">
Detail page has: "Ticket price: $X", "Overall odds of winning: 1 in X.XX",
table with Tier Prize Description | Total Prizes | Estimated Prizes Remaining.
EV computed by deriving per-tier odds from overall_odds * total_prizes / tier_total.
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://myarkansaslottery.com/games/instant"
BASE_URL = "https://myarkansaslottery.com"


class ArkansasScraper(BaseScraper):
    state_code = "AR"
    state_name = "Arkansas"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not re.match(r"^/games/[a-z0-9-]+$", href):
                continue
            if href in seen or href == "/games/instant":
                continue
            seen.add(href)

            # Name from img alt: "$200,000 Bonus Cash - Game No. 895" → strip " - Game No. XXX"
            img = a.find("img")
            alt = img.get("alt", "").strip() if img else ""
            name = re.sub(r"\s*-\s*Game\s+No\.?\s*\d+\s*$", "", alt, flags=re.I).strip()
            if not name:
                continue

            slug = href.split("/")[-1]
            detail_url = BASE_URL + href
            price, tiers, overall_odds = None, [], None
            try:
                price, tiers, overall_odds = self._scrape_detail(detail_url)
            except Exception as e:
                logger.debug("AR detail failed for %s: %s", name, e)

            if not price:
                continue

            games.append(self.build_game(
                game_id=slug,
                name=name,
                price=price,
                tiers=tiers,
                overall_odds=overall_odds,
                detail_url=detail_url,
            ))

        logger.info("AR: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> tuple[float | None, list, float | None]:
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        price = None
        pm = re.search(r"ticket\s+price[:\s]+\$?([\d.]+)", page_text, re.I)
        if pm:
            price = float(pm.group(1))

        overall_odds = None
        om = re.search(r"overall\s+odds.*?1\s+in\s+([\d.]+)", page_text, re.I)
        if om:
            overall_odds = float(om.group(1))

        # Table: Tier Prize Description | Total Prizes | Estimated Prizes Remaining | ...
        tiers = []
        total_prizes = 0
        for table in soup.find_all("table"):
            hdrs = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not any("prize" in h or "tier" in h for h in hdrs):
                continue

            prize_col = total_col = rem_col = None
            for i, h in enumerate(hdrs):
                if prize_col is None and ("tier" in h or "description" in h):
                    prize_col = i
                elif total_col is None and "total" in h and "amount" not in h:
                    total_col = i
                elif rem_col is None and "remaining" in h and "amount" not in h:
                    rem_col = i

            if prize_col is None:
                prize_col = 0
            if total_col is None:
                total_col = 1
            if rem_col is None:
                rem_col = 2

            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= max(prize_col, total_col, rem_col):
                    continue
                prize = parse_prize_amount(cells[prize_col].get_text(strip=True))
                if not prize or prize <= 0:
                    continue
                try:
                    orig = int(cells[total_col].get_text(strip=True).replace(",", ""))
                except (ValueError, TypeError):
                    orig = None
                try:
                    rem = int(cells[rem_col].get_text(strip=True).replace(",", ""))
                except (ValueError, TypeError):
                    rem = None

                if orig:
                    total_prizes += orig

                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": None,
                    "prizes_remaining": rem,
                    "prizes_total": orig,
                })
            if tiers:
                break

        # Derive per-tier odds from total tickets estimated by overall_odds * total_prizes
        if overall_odds and total_prizes > 0:
            total_tickets_est = round(total_prizes * overall_odds)
            for t in tiers:
                if t["prizes_total"] and total_tickets_est:
                    t["odds_one_in"] = round(total_tickets_est / t["prizes_total"], 4)

        return price, tiers, overall_odds
