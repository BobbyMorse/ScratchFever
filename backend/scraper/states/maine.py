"""
Maine State Lottery scratch-off scraper.

Second-chance: ME runs second-chance through club.mainelottery.com but
the promotions index there is JS-rendered (empty server-side) and per-
promo pages live at hand-curated slugs that change. has_second_chance
stays FALSE for all ME games until a stable per-game source is found.

Data sources
------------
1. Per-price listing pages (mainelottery.com/instant/scratch{N}dollar.html)
   → discover active games: name, image, detail URL (a maine.gov ?id=... page).

2. Unclaimed prizes table (mainelottery.com/players_info/unclaimed_prizes.html)
   → per game: Game #, Percent Unsold, Total Unclaimed ($), and the published
   top prize tiers with unclaimed counts. The HTML is a single
   <table class="tbstriped"> where each game starts with a fully-populated row
   and additional tier rows follow with the first 5 cells blank.

3. maine.gov detail page → overall odds + total tickets printed.

EV formula
----------
  tickets_remaining = total_tickets × (percent_unsold / 100)
  top_tier_value    = Σ (prize_amount × prizes_remaining) for listed tiers
  small_unclaimed   = max(0, total_unclaimed − top_tier_value)
  small_in_unsold   = small_unclaimed × (percent_unsold / 100)
  prize_pool_unsold = top_tier_value + small_in_unsold
  ev_per_ticket     = prize_pool_unsold / tickets_remaining
  return_pct        = ev_per_ticket / price × 100

Maine's published "Total Unclaimed" is the dollar value of all prizes not yet
redeemed across every printed ticket — including small prizes won on already-
sold tickets that just haven't been cashed in. Naively dividing by unsold
tickets balloons EV as a game sells through (the unredeemed-on-sold pool stays
roughly constant while the unsold denominator shrinks).

The top-tier counts are reliable (state tracks them), so we keep top-tier
value at face. For the remaining "small-prize unclaimed" pool we pro-rate by
percent_unsold — i.e., assume those small-prize dollars are distributed
proportionally to remaining inventory. Marked ev_approximate=True because the
pro-rate is a heuristic, not a measurement.
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, calculate_jackpot_odds

logger = logging.getLogger(__name__)

BASE_URL = "https://www.mainelottery.com"
UNCLAIMED_URL = f"{BASE_URL}/players_info/unclaimed_prizes.html"
PRICE_PAGES = [
    (1,  "/instant/scratch1dollar.html"),
    (2,  "/instant/scratch2dollar.html"),
    (3,  "/instant/scratch3dollar.html"),
    (5,  "/instant/scratch5dollar.html"),
    (10, "/instant/scratch10dollar.html"),
    (20, "/instant/scratch20dollar.html"),
    (25, "/instant/scratch25dollar.html"),
    (30, "/instant/scratch30dollar.html"),
]


def _norm(name: str) -> str:
    """Normalize a game name for fuzzy matching across the two ME sources.
    Strips backtick/apostrophe variants and non-alphanumerics, uppercases.
    """
    return re.sub(r"[^A-Z0-9]", "", name.upper())


class MaineScraper(BaseScraper):
    state_code = "ME"
    state_name = "Maine"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        unclaimed = self._scrape_unclaimed_table()
        logger.info("ME: parsed %d games from unclaimed prizes table", len(unclaimed))

        games = []
        seen_ids = set()

        for price, path in PRICE_PAGES:
            url = BASE_URL + path
            try:
                soup = self.soup(url)
            except Exception as e:
                logger.debug("ME listing error for %s: %s", path, e)
                continue

            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "maine.gov/tools/whatsnew" not in href:
                    continue

                name = a.get_text(strip=True)
                if not name or len(name) < 2:
                    continue

                norm = _norm(name)
                unclaimed_data = unclaimed.get(norm)

                # Skip games with ≤0% unsold (completely or effectively sold out).
                if unclaimed_data and unclaimed_data.get("percent_unsold", 0) <= 0:
                    continue

                # Prefer the official ME game number from the unclaimed table for a
                # stable game_id; fall back to the maine.gov topic id.
                if unclaimed_data:
                    game_id = f"me{unclaimed_data['game_no']}"
                else:
                    m = re.search(r"id=(\d+)", href)
                    game_id = f"me-{m.group(1)}" if m else f"me-{norm[:20]}"

                if game_id in seen_ids:
                    continue
                seen_ids.add(game_id)

                image_url = None
                parent = a.find_parent(["li", "div", "td", "article"])
                img_el = a.find("img") or (parent.find("img") if parent else None)
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src", "")
                    if src:
                        image_url = (BASE_URL + src) if src.startswith("/") else src

                try:
                    overall_odds, total_tickets = self._scrape_detail(href)
                except Exception as e:
                    logger.debug("ME detail error for %s: %s", name, e)
                    overall_odds, total_tickets = None, None

                game = self._build_game(
                    game_id=game_id,
                    name=name,
                    price=float(price),
                    detail_url=href,
                    image_url=image_url,
                    overall_odds=overall_odds,
                    total_tickets=total_tickets,
                    unclaimed_data=unclaimed_data,
                )
                games.append(game)

        with_ev = sum(1 for g in games if g.get("ev") is not None)
        logger.info("ME: %d games scraped (%d with EV)", len(games), with_ev)
        return games

    def _build_game(
        self,
        *,
        game_id: str,
        name: str,
        price: float,
        detail_url: str,
        image_url: str | None,
        overall_odds: float | None,
        total_tickets: int | None,
        unclaimed_data: dict | None,
    ) -> dict:
        """Build the game dict with EV derived from Maine's total unclaimed pool."""
        tiers: list[dict] = []
        tickets_remaining = None
        ev = None
        return_pct = None
        top_prize = 0.0
        top_prize_remaining = None
        jackpot_odds = None
        prize_pool_left = None

        if unclaimed_data:
            for amt, cnt in unclaimed_data["top_tiers"]:
                tiers.append({
                    "prize_amount":     amt,
                    "odds_one_in":      None,
                    "prizes_total":     None,
                    "prizes_remaining": cnt,
                })

            if tiers:
                top = max(tiers, key=lambda t: t["prize_amount"])
                top_prize = top["prize_amount"]
                top_prize_remaining = top["prizes_remaining"]

            percent_unsold = unclaimed_data["percent_unsold"]
            total_unclaimed = unclaimed_data["total_unclaimed"]

            if total_tickets and percent_unsold is not None:
                tickets_remaining = int(round(total_tickets * percent_unsold / 100))
                # Skip games that calculate to zero remaining tickets (sold out).
                if tickets_remaining == 0:
                    tickets_remaining = None

            top_tier_value = sum(amt * cnt for amt, cnt in unclaimed_data["top_tiers"])
            small_unclaimed = max(0.0, (total_unclaimed or 0.0) - top_tier_value)
            small_in_unsold = small_unclaimed * (percent_unsold or 0.0) / 100.0
            prize_pool_left = top_tier_value + small_in_unsold

            if (
                tickets_remaining
                and tickets_remaining > 0
                and prize_pool_left > 0
                and price > 0
            ):
                ev_per_ticket = prize_pool_left / tickets_remaining
                ev = round(ev_per_ticket - price, 4)
                return_pct = round(ev_per_ticket / price * 100, 2)

            jackpot_odds = calculate_jackpot_odds(tiers, tickets_remaining)

        return {
            "game_id":             game_id,
            "name":                name,
            "price":                price,
            "ev":                   ev,
            "return_pct":           return_pct,
            "overall_odds_one_in":  overall_odds,
            "top_prize":            top_prize,
            "top_prize_remaining":  top_prize_remaining,
            "jackpot_odds_one_in":  jackpot_odds,
            "total_tickets":        total_tickets,
            "tickets_remaining":    tickets_remaining,
            "prize_pool_left":      prize_pool_left,
            "detail_url":           detail_url,
            "image_url":            image_url,
            "end_date":             None,
            "ev_approximate":       True,
            "tiers":                tiers,
        }

    def _scrape_detail(self, url: str) -> tuple[float | None, int | None]:
        """Returns (overall_odds, total_tickets) from a maine.gov game page."""
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        overall_odds = None
        m = re.search(r"OVERALL\s+ODDS\s+(?:OF\s+WINNING\s+)?1[:\s]+([\d.,]+)", page_text, re.IGNORECASE)
        if m:
            try:
                overall_odds = float(m.group(1).replace(",", ""))
            except ValueError:
                pass

        total_tickets = None
        m2 = re.search(r"Tickets?\s+Printed[-‐-―:\s]+([\d,]+)", page_text, re.IGNORECASE)
        if m2:
            try:
                total_tickets = int(m2.group(1).replace(",", ""))
            except ValueError:
                pass

        return overall_odds, total_tickets

    def _scrape_unclaimed_table(self) -> dict[str, dict]:
        """Parse the unclaimed prizes table.

        Returns {normalized_name: {game_no, price, percent_unsold,
        total_unclaimed, top_tiers: [(prize_amount, count), ...]}}.

        Continuation rows have the first 5 cells blank/whitespace and only the
        last two cells populated (additional tier + count for the previous game).
        """
        try:
            soup = self.soup(UNCLAIMED_URL)
        except Exception as e:
            logger.warning("ME: could not fetch unclaimed prizes table: %s", e)
            return {}

        table = soup.find("table", class_="tbstriped")
        if table is None:
            logger.warning("ME: unclaimed prizes table not found in HTML")
            return {}

        result: dict[str, dict] = {}
        current: dict | None = None

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 7:
                continue
            if any(c.name == "th" for c in cells):
                continue  # header row

            texts = [c.get_text(strip=True) for c in cells]
            price_cell, gameno_cell, name_cell, pct_cell, total_cell, prize_cell, count_cell = texts[:7]

            if price_cell:
                # New game header row
                price = parse_prize_amount(price_cell)
                try:
                    percent_unsold = float(pct_cell)
                except ValueError:
                    percent_unsold = None
                total_unclaimed = parse_prize_amount(total_cell)
                prize_amt = parse_prize_amount(prize_cell)
                try:
                    prize_cnt = int(count_cell.replace(",", ""))
                except ValueError:
                    prize_cnt = None

                if not name_cell or percent_unsold is None or total_unclaimed is None:
                    current = None
                    continue

                top_tiers: list[tuple[float, int]] = []
                if prize_amt and prize_amt > 0 and prize_cnt:
                    top_tiers.append((prize_amt, prize_cnt))

                current = {
                    "game_no":         gameno_cell,
                    "price":           price,
                    "percent_unsold":  percent_unsold,
                    "total_unclaimed": total_unclaimed,
                    "top_tiers":       top_tiers,
                }
                result[_norm(name_cell)] = current
                # Also index without a leading "$N" / "$N,NNN" prefix so
                # "$500,000 ROYAL CASH" (unclaimed table) matches "ROYAL CASH"
                # (listing page).
                stripped = re.sub(r"^\s*\$[\d,]+\s+", "", name_cell)
                if stripped and stripped != name_cell:
                    result.setdefault(_norm(stripped), current)
            else:
                # Continuation row: extra tier for the current game.
                if current is None:
                    continue
                prize_amt = parse_prize_amount(prize_cell)
                try:
                    prize_cnt = int(count_cell.replace(",", ""))
                except ValueError:
                    prize_cnt = None
                if prize_amt and prize_amt > 0 and prize_cnt:
                    current["top_tiers"].append((prize_amt, prize_cnt))

        return result
