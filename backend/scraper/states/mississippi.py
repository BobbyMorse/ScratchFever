"""
Mississippi Lottery scratch-off scraper.

Second-chance: detected per-game from the prize table. MS marks 2nd-chance
tiers by putting the literal text "2nd Chance Prize" in the Original Prize
Count column (e.g. Gold 7s has a $200,000 2nd-chance row). Any such row
sets has_second_chance=True. The portal at secondchance.mslottery.com is
login-gated and the promo blog archives are too churny to parse.

Listing: https://www.mslottery.com/gamestatus/active/
Each game: <a href="/instantgames/slug/"> — no name/price in listing HTML.
Detail page: H1 "Name ($X)", "Ticket Price $X", "Overall Odds 1:X.XX",
table with Prize Value | Original Prize Count | Remaining Prize Count.
EV computed from remaining prize data.

Detail fetches are parallelized via the shared DETAIL_POOL — fetching
sequentially used to push the scraper past the runner's 120s timeout
ceiling once the active-game count grew past ~25.
"""
import re
import time
import logging
from concurrent.futures import as_completed

import requests
from bs4 import BeautifulSoup

from backend.scraper._shared_pool import DETAIL_POOL
from backend.scraper.base import BaseScraper, HEADERS
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.mslottery.com/gamestatus/active/"
BASE_URL = "https://www.mslottery.com"

_DETAIL_TIMEOUT = 20
# Soft budget so the scraper always returns inside the runner's 120s wall,
# even if mslottery.com is slow. Anything not fetched this run gets picked
# up next cycle.
_FETCH_BUDGET_S = 90


def _parse_detail_soup(soup: BeautifulSoup):
    """Pure parser for an MS game detail page. Returns a dict of fields or
    None when the page didn't yield enough to build a game."""
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

    def rank(u: str) -> tuple:
        uu = u.upper()
        is_thumb = bool(re.search(r"-\d+X\d+\.[A-Z]+$", uu))
        is_c = "FRONT-C" in uu
        return (is_thumb, not is_c)
    for u in sorted(candidates, key=rank):
        image_url = u if u.startswith("http") else (BASE_URL + u)
        break

    # Name from H1. Historically the H1 included the price in parens
    # ("Wheel of Fortune ($10)"); newer pages drop it ("Wheel of Fortune"),
    # so strip a trailing "($X)" if present and otherwise use the H1 verbatim.
    name = price = None
    h1 = soup.find("h1")
    if h1:
        h1_text = h1.get_text(strip=True)
        pm = re.search(r"\s*\(\$(\d+)\)\s*$", h1_text)
        if pm:
            price = float(pm.group(1))
            name = h1_text[:pm.start()].strip()
        else:
            name = h1_text
    # Price almost always comes from the "Ticket Price $X" info row now.
    if not price:
        pm2 = re.search(r"ticket\s+price[:\s]+\$?([\d.]+)", page_text, re.I)
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
    has_second_chance = False

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
                orig_text = cells[orig_col].get_text(strip=True)
                if re.search(r"(?:2nd|second)\s*chance", orig_text, re.I):
                    has_second_chance = True
                try:
                    orig = int(orig_text.replace(",", ""))
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

    if not name or not price:
        return None

    # Estimate ticket counts from overall odds * total prizes
    tickets_remaining = total_tickets = None
    if overall_odds and total_prizes > 0:
        total_tickets = round(total_prizes * overall_odds)
        if remaining_prizes > 0:
            tickets_remaining = round(total_tickets * remaining_prizes / total_prizes)

    return {
        "name": name,
        "price": price,
        "tiers": tiers,
        "overall_odds": overall_odds,
        "tickets_remaining": tickets_remaining,
        "total_tickets": total_tickets,
        "image_url": image_url,
        "has_second_chance": has_second_chance,
    }


def _fetch_game(slug: str, detail_url: str):
    """Fetch and parse one MS detail page. Runs in a DETAIL_POOL worker."""
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=_DETAIL_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        return slug, detail_url, _parse_detail_soup(soup)
    except Exception as e:
        logger.debug("MS detail failed for %s: %s", slug, e)
        return slug, detail_url, None


class MississippiScraper(BaseScraper):
    state_code = "MS"
    state_name = "Mississippi"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)

        slug_to_url: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/instantgames/" not in href:
                continue
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug == "instantgames" or slug in slug_to_url:
                continue
            slug_to_url[slug] = (BASE_URL + href) if href.startswith("/") else href

        logger.info("MS: %d game detail pages to fetch", len(slug_to_url))

        results: dict[str, tuple[dict, str]] = {}
        deadline = time.monotonic() + _FETCH_BUDGET_S
        future_to_slug = {
            DETAIL_POOL.submit(_fetch_game, slug, url): slug
            for slug, url in slug_to_url.items()
        }
        for future in as_completed(future_to_slug):
            slug, detail_url, parsed = future.result()
            if parsed:
                results[slug] = (parsed, detail_url)
            if time.monotonic() >= deadline:
                remaining = sum(1 for f in future_to_slug if not f.done())
                logger.warning(
                    "MS: fetch budget hit, %d parsed, %d skipped this run",
                    len(results), remaining,
                )
                for f in future_to_slug:
                    if not f.done():
                        f.cancel()
                break

        games = []
        for slug, (parsed, detail_url) in results.items():
            games.append(self.build_game(
                game_id=slug,
                name=parsed["name"],
                price=parsed["price"],
                tiers=parsed["tiers"],
                overall_odds=parsed["overall_odds"],
                tickets_remaining=parsed["tickets_remaining"],
                total_tickets=parsed["total_tickets"],
                detail_url=detail_url,
                image_url=parsed["image_url"],
            ))

        logger.info("MS: %d games scraped", len(games))
        return games
