"""
Washington's Lottery scratch-off scraper.

Second-chance: WA's second-chance subdomain (secondchance.walottery.com)
refuses bot traffic and My Lottery 360 requires login. No public per-game
source. has_second_chance stays FALSE for all WA games.

Prize tier data:  https://walottery.com/Scratch/TopPrizesRemaining.aspx?price=X
  Server-rendered per price point. Each game is a <div class="prizes-remaining-item">.
  The child <header> contains: "GAME NAME $PRICE | GAME_ID [Last Day To Redeem: DATE]".
  A nested <table> has columns: Prize Amount, Total Prizes, Prizes Paid, Prizes Remaining.

Overall odds:     https://walottery.com/Scratch/Explorer.aspx?id=GAME_ID (JS-rendered)
  Fields: Overall Odds (1 in X.XX), Tickets Printed.
  Requires Playwright; the browser is shared across all per-game calls.
"""
from __future__ import annotations
import re
import logging
from datetime import date, datetime
from bs4 import BeautifulSoup, Tag
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

PRICES = [1, 2, 3, 5, 10, 20, 30]
BASE_URL = "https://walottery.com"
TOP_PRIZES_URL = f"{BASE_URL}/Scratch/TopPrizesRemaining.aspx"
EXPLORER_URL = f"{BASE_URL}/Scratch/Explorer.aspx"


def _int(text: str) -> int | None:
    try:
        return int(str(text).strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def _parse_date(text: str) -> str | None:
    """Return ISO date string or None if expired / unparseable."""
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text.strip(), fmt).date()
            return None if d < date.today() else d.isoformat()
        except ValueError:
            continue
    return None


def _parse_prize_text(text: str) -> float | None:
    """Handle WA prize description formats beyond plain dollar amounts."""
    t = text.strip()
    if not t:
        return None
    # Strip surrounding parentheses: "($50 x 10)" → "$50 x 10"
    t = re.sub(r"^\((.+)\)$", r"\1", t).strip()
    # Life annuity — not calculable from this string alone. Dropping the tier
    # is safe because the base annuity heuristic has a ratio guard that
    # refuses to NPV-explode a much smaller cash tier when the real for-life
    # tier is missing from the data.
    if re.search(r"\blife\b", t, re.I):
        return None
    # "$40,000/yr/25 years" → nominal total
    m = re.match(r"\$?([\d,]+)/yr/(\d+)\s*years?", t, re.I)
    if m:
        return float(m.group(1).replace(",", "")) * int(m.group(2))
    # "($20 x 2) + $10" → 50
    m = re.match(r"\(\$?([\d,]+)\s*[xX×]\s*(\d+)\)\s*\+\s*\$?([\d,]+)", t)
    if m:
        return (float(m.group(1).replace(",", "")) * int(m.group(2))
                + float(m.group(3).replace(",", "")))
    # "$50 x 10" or "$50 × 10" → 500
    m = re.match(r"\$?([\d,]+)\s*[xX×]\s*(\d+)", t)
    if m:
        return float(m.group(1).replace(",", "")) * int(m.group(2))
    # "$10 w/5X" → 50 (base × multiplier)
    m = re.match(r"\$?([\d,]+)\s*w/(\d+)[Xx]", t)
    if m:
        return float(m.group(1).replace(",", "")) * int(m.group(2))
    return parse_prize_amount(t)


class WashingtonScraper(PlaywrightScraper):
    state_code = "WA"
    state_name = "Washington"
    base_url = BASE_URL
    scraper_timeout = 900  # ~60 games × ~10 s Playwright each + overhead

    def scrape(self) -> list[dict]:
        # ── Phase 1: tier data via HTTP (7 price-point pages) ──────────────────
        games_by_id: dict[str, dict] = {}
        for price in PRICES:
            url = f"{TOP_PRIZES_URL}?price={price}"
            try:
                soup = self.soup(url)
                self._parse_top_prizes(soup, float(price), games_by_id)
            except Exception as exc:
                logger.warning("WA: price=%d page failed: %s", price, exc)

        logger.info("WA: %d games found in TopPrizesRemaining pages", len(games_by_id))

        # ── Phase 2: overall odds via Playwright (one call per game) ───────────
        games: list[dict] = []
        for game_id, gd in games_by_id.items():
            overall_odds, total_tickets = None, None
            try:
                esoup = self.pw_soup(
                    f"{EXPLORER_URL}?id={game_id}",
                    wait_for="networkidle",
                    extra_wait_ms=2_500,
                    timeout=30_000,
                )
                overall_odds, total_tickets = self._parse_explorer(esoup)
            except Exception as exc:
                logger.debug("WA: Explorer failed for %s: %s", game_id, exc)

            tiers = gd["tiers"]

            # IL/MA formula: tickets_remaining = overall_odds × Σ prizes_remaining
            tickets_remaining = None
            if overall_odds and tiers:
                total_rem = sum(t.get("prizes_remaining") or 0 for t in tiers)
                if total_rem > 0:
                    tickets_remaining = round(overall_odds * total_rem)

            # Back-fill per-tier odds from total_tickets
            if total_tickets:
                for t in tiers:
                    pt = t.get("prizes_total")
                    if pt:
                        t["odds_one_in"] = round(total_tickets / pt, 2)

            games.append(self.build_game(
                game_id=game_id,
                name=gd["name"],
                price=gd["price"],
                tiers=tiers,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                overall_odds=overall_odds,
                detail_url=f"{EXPLORER_URL}?id={game_id}",
                image_url=f"{BASE_URL}/scratch/assets/imgs/tickets/grid/{game_id}.jpg",
                end_date=gd.get("end_date"),
            ))

        logger.info("WA: %d games built", len(games))
        return games

    # ── TopPrizesRemaining parser ───────────────────────────────────────────────

    def _parse_top_prizes(self, soup: BeautifulSoup, price: float,
                          games_by_id: dict):
        """
        Each game sits in <div class="prizes-remaining-item">.
        Its child <header> text: "GAME NAME $PRICE | GAME_ID [Last Day To Redeem: DATE]".
        The Explorer link lives inside that header.
        """
        links = soup.find_all("a", href=re.compile(r"Explorer\.aspx\?id=\d+", re.I))
        for link in links:
            m = re.search(r"[?&]id=(\d+)", link["href"], re.I)
            if not m:
                continue
            game_id = m.group(1)
            if game_id in games_by_id:
                continue

            # Header element is the direct parent of the link
            header = link.parent
            header_text = header.get_text(" ", strip=True) if header else ""

            # Name: everything before " $PRICE | GAME_ID"
            name_m = re.match(r"^(.*?)\s+\$[\d.]+\s*\|", header_text)
            name = name_m.group(1).strip() if name_m else f"WA Game {game_id}"

            # End date from header text
            end_date = None
            date_m = re.search(
                r"Last Day To Redeem:\s*(\d{1,2}/\d{1,2}/\d{2,4})",
                header_text, re.I,
            )
            if date_m:
                parsed = _parse_date(date_m.group(1))
                if parsed is None:
                    continue  # expired — skip
                end_date = parsed

            # Prize table: walk up to prizes-remaining-item div
            item_div = self._find_item_div(link)
            prize_table = None
            if item_div:
                for tbl in item_div.find_all("table"):
                    hdrs = " ".join(
                        th.get_text(strip=True).lower() for th in tbl.find_all("th")
                    )
                    if "total" in hdrs and "remaining" in hdrs:
                        prize_table = tbl
                        break

            tiers = self._parse_tier_table(prize_table) if prize_table else []
            games_by_id[game_id] = {
                "name": name,
                "price": price,
                "end_date": end_date,
                "tiers": tiers,
            }

    def _find_item_div(self, link: Tag) -> Tag | None:
        """Walk up the DOM to find the prizes-remaining-item container."""
        node = link
        for _ in range(6):
            node = node.parent
            if not node:
                return None
            if "prizes-remaining-item" in (node.get("class") or []):
                return node
        return None

    def _parse_tier_table(self, table: Tag) -> list[dict]:
        """Parse Prize Amount / Total Prizes / Prizes Paid / Prizes Remaining."""
        ths = table.find_all("th")
        headers = [th.get_text(strip=True).lower() for th in ths]

        col_prize = col_total = col_paid = col_remaining = None
        for i, h in enumerate(headers):
            if col_remaining is None and "remaining" in h:
                col_remaining = i
            elif col_paid is None and ("paid" in h or "claimed" in h):
                col_paid = i
            elif col_total is None and ("total" in h or "print" in h):
                col_total = i
            elif col_prize is None and any(k in h for k in ("prize", "amount")):
                col_prize = i

        if col_prize is None:
            col_prize = 0

        tiers = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if not cells or len(cells) < 2:
                continue

            prize_text = (
                cells[col_prize].get_text(strip=True) if col_prize < len(cells) else ""
            )
            prize = _parse_prize_text(prize_text)
            if not prize or prize <= 0:
                continue

            total = remaining = None
            if col_total is not None and col_total < len(cells):
                total = _int(cells[col_total].get_text(strip=True))
            if col_remaining is not None and col_remaining < len(cells):
                remaining = _int(cells[col_remaining].get_text(strip=True))
            elif col_paid is not None and col_paid < len(cells) and total is not None:
                paid = _int(cells[col_paid].get_text(strip=True))
                if paid is not None:
                    remaining = max(total - paid, 0)

            tiers.append({
                "prize_amount": prize,
                "odds_one_in": None,
                "prizes_total": total,
                "prizes_remaining": remaining,
            })

        return tiers

    # ── Explorer page parser ────────────────────────────────────────────────────

    def _parse_explorer(self, soup: BeautifulSoup) -> tuple[float | None, int | None]:
        """Extract Overall Odds and Tickets Printed from the JS-rendered Explorer page."""
        text = soup.get_text(" ", strip=True)

        overall_odds = None
        m = re.search(r"[Oo]verall\s+[Oo]dds[^0-9]*1\s+in\s+([\d,.]+)", text)
        if m:
            try:
                overall_odds = float(m.group(1).replace(",", ""))
            except ValueError:
                pass

        total_tickets = None
        m = re.search(r"[Tt]ickets?\s+[Pp]rinted[^0-9]*([\d,]+)", text)
        if m:
            total_tickets = _int(m.group(1))

        return overall_odds, total_tickets
