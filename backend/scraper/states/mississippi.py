"""
Mississippi Lottery scratch-off scraper.

Second-chance: MS's 2nd Chance portal at secondchance.mslottery.com gates
the eligible-games list behind login. Per-promo blog posts at
/promotype/second-chance-drawings/ name eligible games for individual
drawings but parsing rolling promo archives is fragile. has_second_chance
stays FALSE for all MS games until a stable per-game source is found.

Listing: https://www.mslottery.com/gamestatus/active/
Each game: <a href="/instantgames/slug/"> — no name/price in listing HTML.
Detail page: H1 "Name ($X)", "Ticket Price $X", "Overall Odds 1:X.XX",
table with Prize Value | Original Prize Count | Remaining Prize Count.
EV computed from remaining prize data.
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.mslottery.com/gamestatus/active/"
BASE_URL = "https://www.mslottery.com"


class MississippiScraper(BaseScraper):
    state_code = "MS"
    state_name = "Mississippi"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/instantgames/" not in href:
                continue
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug in seen or slug == "instantgames":
                continue
            seen.add(slug)

            detail_url = (BASE_URL + href) if href.startswith("/") else href
            name, price, tiers, overall_odds, tickets_remaining, total_tickets, image_url = \
                None, None, [], None, None, None, None
            try:
                name, price, tiers, overall_odds, tickets_remaining, total_tickets, image_url = \
                    self._scrape_detail(detail_url)
            except Exception as e:
                logger.debug("MS detail failed for %s: %s", slug, e)

            if not name or not price:
                continue

            games.append(self.build_game(
                game_id=slug,
                name=name,
                price=price,
                tiers=tiers,
                overall_odds=overall_odds,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                detail_url=detail_url,
                image_url=image_url,
            ))

        logger.info("MS: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str):
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        # Image URL: mslottery.com lazy-loads, so the real URL is in data-src.
        # Prefer the full-size FRONT-C (front, complete/uncovered) over thumbnails/back.
        image_url = None
        candidates = []
        for img in soup.find_all("img"):
            src = (img.get("data-src") or img.get("src") or "").split("?")[0]
            if "wp-content/uploads" not in src or "FRONT" not in src.upper():
                continue
            candidates.append(src)
        # Rank: prefer "scaled" or no size suffix; deprioritize thumbnails like -210x210
        def rank(u: str) -> tuple:
            uu = u.upper()
            is_thumb = bool(re.search(r"-\d+X\d+\.[A-Z]+$", uu))
            is_c = "FRONT-C" in uu  # front cover, no winning ticket overlay
            return (is_thumb, not is_c)
        for u in sorted(candidates, key=rank):
            image_url = u if u.startswith("http") else (BASE_URL + u)
            break

        # Name and price from H1: "Wheel of Fortune ($10)"
        name = price = None
        h1 = soup.find("h1")
        if h1:
            h1_text = h1.get_text(strip=True)
            pm = re.search(r"\(\$(\d+)\)\s*$", h1_text)
            if pm:
                price = float(pm.group(1))
                name = h1_text[:pm.start()].strip()
        if not price:
            pm2 = re.search(r"ticket\s+price\s+\$?([\d.]+)", page_text, re.I)
            if pm2:
                price = float(pm2.group(1))

        overall_odds = None
        om = re.search(r"overall\s+odds[:\s]+1[:\s]+([\d.]+)", page_text, re.I)
        if om:
            overall_odds = float(om.group(1))

        # Table: Prize Value | Original Prize Count | Remaining Prize Count
        tiers = []
        total_prizes = 0
        remaining_prizes = 0

        for table in soup.find_all("table"):
            hdrs = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not any("prize" in h or "value" in h for h in hdrs):
                continue

            prize_col = orig_col = rem_col = None
            for i, h in enumerate(hdrs):
                if ("prize" in h or "value" in h) and prize_col is None:
                    prize_col = i
                elif "original" in h or ("total" in h and "prize" not in h):
                    orig_col = i
                elif "remaining" in h:
                    rem_col = i

            if prize_col is None:
                continue

            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= prize_col:
                    continue
                prize = parse_prize_amount(cells[prize_col].get_text(strip=True))
                if not prize or prize <= 0:
                    continue

                orig = None
                if orig_col is not None and len(cells) > orig_col:
                    try:
                        orig = int(cells[orig_col].get_text(strip=True).replace(",", ""))
                    except (ValueError, TypeError):
                        pass

                rem = None
                if rem_col is not None and len(cells) > rem_col:
                    try:
                        rem = int(cells[rem_col].get_text(strip=True).replace(",", ""))
                    except (ValueError, TypeError):
                        pass

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

        # Estimate ticket counts from overall odds * total prizes
        tickets_remaining = total_tickets = None
        if overall_odds and total_prizes > 0:
            total_tickets = round(total_prizes * overall_odds)
            if remaining_prizes > 0:
                tickets_remaining = round(total_tickets * remaining_prizes / total_prizes)

        return name, price, tiers, overall_odds, tickets_remaining, total_tickets, image_url
