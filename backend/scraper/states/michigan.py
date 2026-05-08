"""
Michigan Lottery scratch-off scraper.
Listing: https://www.michiganlottery.com/games (JS-rendered)
  In-store scratch tickets: /games/{id}-instore-instant-{slug}
Detail:  https://www.michiganlottery.com/games/{id}-instore-instant-{slug}
  Text: "Price: $X.XX  Overall Odds: 1 in X.XX"
  Table columns: PRIZE | REMAINING | START (remaining/total counts, no per-tier odds)
  EV computed from overall odds: total_tickets = total_prizes_printed * overall_odds
"""
import re
import logging
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.michiganlottery.com/games"
BASE_URL = "https://www.michiganlottery.com"

_INSTORE_RE = re.compile(r"^/games/\d+-instore-instant-", re.I)


class MichiganScraper(PlaywrightScraper):
    state_code = "MI"
    state_name = "Michigan"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.pw_soup(GAMES_URL, wait_for="load", selector=None, timeout=30_000)

        # Give the page a moment to finish rendering then re-fetch content
        # (pw_soup already waits for 'load', additional JS settle handled by detail pages)
        seen = set()
        game_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if _INSTORE_RE.match(href) and href not in seen:
                seen.add(href)
                game_links.append(href)

        logger.info("MI: found %d in-store scratch game links", len(game_links))

        games = []
        for href in game_links:
            detail_url = BASE_URL + href
            slug = href.split("/")[-1]
            try:
                game = self._scrape_detail(detail_url, slug)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("MI detail failed for %s: %s", slug, e)

        logger.info("MI: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str, slug: str) -> dict | None:
        soup = self.pw_soup(url, wait_for="load", timeout=20_000)
        page_text = soup.get_text(" ", strip=True)

        # Name from h1
        name_el = soup.select_one("h1")
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            name = re.sub(r"^\d+-instore-instant-", "", slug).replace("-", " ").title()

        # Price: "Price: $10.00"
        price = None
        pm = re.search(r"Price:\s*\$?([\d.]+)", page_text, re.I)
        if pm:
            price = float(pm.group(1))
        if not price:
            pm = re.search(r"\$(\d+)\s+(?:ticket|scratch)", page_text, re.I)
            if pm:
                price = float(pm.group(1))
        if not price:
            return None

        # Overall odds: "Overall Odds: 1 in 3.68"
        overall_odds = None
        om = re.search(r"Overall\s+Odds[:\s]+1\s+in\s+([\d,.]+)", page_text, re.I)
        if om:
            overall_odds = float(om.group(1).replace(",", ""))

        # Game ID: leading digits from slug
        gid_m = re.match(r"(\d+)-", slug)
        game_id = gid_m.group(1) if gid_m else slug

        # Prize table: PRIZE | REMAINING | START
        tiers = []
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).upper() for th in table.find_all("th")]
            if "PRIZE" not in " ".join(headers):
                continue
            col = {}
            for i, h in enumerate(headers):
                if "PRIZE" in h:
                    col["prize"] = i
                elif "REMAINING" in h or "LEFT" in h:
                    col["remaining"] = i
                elif "START" in h or "TOTAL" in h or "INITIAL" in h or "PRINT" in h:
                    col["total"] = i

            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue
                prize = parse_prize_amount(cells[col.get("prize", 0)])
                remaining = total = None
                if "remaining" in col and col["remaining"] < len(cells):
                    try:
                        remaining = int(cells[col["remaining"]].replace(",", ""))
                    except ValueError:
                        pass
                if "total" in col and col["total"] < len(cells):
                    try:
                        total = int(cells[col["total"]].replace(",", ""))
                    except ValueError:
                        pass
                if prize and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": None,
                        "prizes_remaining": remaining,
                        "prizes_total": total,
                    })
            if tiers:
                break

        if not tiers:
            return None

        # Compute per-tier odds and tickets_remaining from overall_odds
        total_tickets = None
        tickets_remaining = None
        if overall_odds and overall_odds > 0:
            total_prizes_printed = sum(t["prizes_total"] or 0 for t in tiers)
            total_prizes_remaining = sum(t["prizes_remaining"] or 0 for t in tiers)
            if total_prizes_printed > 0:
                total_tickets = round(overall_odds * total_prizes_printed)
                tickets_remaining = round(overall_odds * total_prizes_remaining)
                for t in tiers:
                    if t["prizes_total"]:
                        t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=url,
        )
