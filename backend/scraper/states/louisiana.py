"""
Louisiana Lottery scratch-off scraper.
Listing: https://louisianalottery.com/scratch-offs/
Each game card is <a href="https://louisianalottery.com/game/NUMBER-slug/" class="so-excerpt">
containing: <h3 class="so-excerpt__title">, <dl> with <div><dt>label<dd>value pairs:
  Ticket Price / Overall Odds / Top Prize / Game No.
Detail page (same URL): table with Tier Prize | Odds of Winning | Total | Claimed | Remaining.
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://louisianalottery.com/scratch-offs/"
BASE_URL = "https://louisianalottery.com"


class LouisianaScraper(BaseScraper):
    state_code = "LA"
    state_name = "Louisiana"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/game/" not in href:
                continue
            if href in seen:
                continue
            seen.add(href)

            h3 = a.find("h3")
            name = h3.get_text(strip=True) if h3 else ""
            if not name:
                continue

            # Price and odds — try dl/dt/dd first (old layout), fall back to regex on card text
            price = None
            overall_odds = None
            for div in a.select("dl div"):
                dt = div.find("dt")
                dd = div.find("dd")
                if not (dt and dd):
                    continue
                label = dt.get_text(strip=True).lower()
                value = dd.get_text(strip=True)
                if "ticket price" in label:
                    price = parse_prize_amount(value)
                elif "overall odds" in label:
                    overall_odds = parse_odds(value.replace(":", " in "))
            if not price:
                card_text = a.get_text(" ", strip=True)
                pm = re.search(r"Ticket\s+Price\s+\$?([\d.]+)", card_text, re.I)
                if pm:
                    price = float(pm.group(1))
                om = re.search(r"Overall\s+Odds\s+1[:\s]+([\d.]+)", card_text, re.I)
                if om:
                    overall_odds = parse_odds(om.group(1))

            if not price:
                continue

            m = re.search(r"/game/(\d+)-", href)
            game_id = m.group(1) if m else re.sub(r"[^a-z0-9]", "", name.lower())[:20]
            detail_url = href if href.startswith("http") else BASE_URL + href

            tiers = []
            try:
                tiers = self._scrape_detail(detail_url)
            except Exception as e:
                logger.debug("LA detail failed for %s: %s", name, e)

            img = a.find("img")
            image_url = img.get("src") if img else None

            total_rem = sum(t.get("prizes_remaining") or 0 for t in tiers)
            total_tot = sum(t.get("prizes_total") or 0 for t in tiers)
            all_have_rem = tiers and all(t.get("prizes_remaining") is not None for t in tiers)
            tickets_remaining = round(overall_odds * total_rem) if overall_odds and all_have_rem else None
            total_tickets = round(overall_odds * total_tot) if overall_odds and total_tot else None

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

        logger.info("LA: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> list[dict]:
        """Fetch game page for Tier Prize | Odds of Winning | Total | Claimed | Remaining table."""
        soup = self.soup(url)
        for table in soup.find_all("table"):
            text = table.get_text().lower()
            if any(k in text for k in ("prize", "odds", "1 in")):
                tiers = self.parse_table_tiers(table)
                if tiers:
                    return tiers
        return []
