"""
DC Lottery (District of Columbia) scratch-off scraper.
Listing: https://dclottery.com/dc-scratchers  (paginated ~20/page via ?page=N)
Detail:  https://dclottery.com/dc-scratchers/{slug}

Prize table columns: Prize Amount | Total Prizes | Prizes Paid | Prizes Remaining
Overall odds labeled as "Odds  1:X.XX" (separate from "Top Prize Odds").
Game number in "Game No" field and encoded in image filename (DC{num}...).
"""
import re
import json
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

GAMES_URL = "https://dclottery.com/dc-scratchers"
BASE_URL  = "https://dclottery.com"

_SLUG_RE  = re.compile(r"/dc-scratchers/([a-z0-9][a-z0-9-]*[a-z0-9])/?$")


class DCScraper(BaseScraper):
    state_code = "DC"
    state_name = "District of Columbia"
    base_url   = BASE_URL

    def scrape(self) -> list[dict]:
        slugs = self._collect_slugs()
        logger.info("DC: %d unique game slugs found", len(slugs))

        games = []
        for slug in slugs:
            url = f"{BASE_URL}/dc-scratchers/{slug}"
            try:
                game = self._scrape_detail(url, slug)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("DC detail failed %s: %s", slug, e)

        logger.info("DC: %d games scraped", len(games))
        return games

    # ── listing ───────────────────────────────────────────────────────────────

    def _collect_slugs(self) -> list[str]:
        seen: set[str] = set()
        slugs: list[str] = []
        consecutive_empty = 0

        for page in range(25):
            url = GAMES_URL if page == 0 else f"{GAMES_URL}?page={page}"
            try:
                soup = self.soup(url)
            except Exception as e:
                logger.debug("DC listing page %d failed: %s", page, e)
                break

            new_count = 0
            for a in soup.find_all("a", href=True):
                m = _SLUG_RE.search(a["href"])
                if not m:
                    continue
                slug = m.group(1)
                if slug not in seen:
                    seen.add(slug)
                    slugs.append(slug)
                    new_count += 1

            if new_count == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
            else:
                consecutive_empty = 0

        return slugs

    # ── detail ────────────────────────────────────────────────────────────────

    def _scrape_detail(self, url: str, slug: str) -> dict | None:
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        # Game name from <h1>
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""
        if not name:
            return None

        # Game number — from Drupal JSON, then "Game No XXXX", then image filename
        game_id = self._extract_game_id(soup, page_text, slug)

        # Price — "Price $X.XX"
        price = None
        m = re.search(r"\bPrice\s+\$\s*([\d]+(?:\.\d{1,2})?)", page_text, re.I)
        if m:
            price = float(m.group(1))
        if not price:
            return None

        # Extract all odds values from page; smallest = overall, largest = top-prize
        overall_odds, top_prize_odds = self._extract_odds(page_text)

        # Image URL — prefer ticket image (DC+digits in filename) over generic banners
        image_url = None
        all_imgs = soup.find_all("img", src=True)
        ticket_img = next(
            (i for i in all_imgs if re.search(r"/DC\d+", i["src"], re.I)),
            None,
        ) or next(
            (i for i in all_imgs if "/sites/default/files/" in i["src"]
             and "inline-images" not in i["src"]),
            None,
        )
        if ticket_img:
            src = ticket_img["src"]
            image_url = (BASE_URL + src) if src.startswith("/") else src
            image_url = image_url.split("?")[0]

        # Prize table
        tiers = []
        for table in soup.find_all("table"):
            text = table.get_text().lower()
            if any(k in text for k in ("prize", "remaining", "paid")):
                tiers = self.parse_table_tiers(table)
                if tiers:
                    break

        # Ticket counts
        # Use top-prize odds × top-prize count for total tickets — mathematically exact
        # (if there are N jackpots and odds are 1:X, there are exactly N×X tickets).
        # This avoids the multi-play / missing-free-ticket issue where "Overall Odds"
        # on DC's site can refer to per-play odds rather than per-ticket odds.
        total_tickets = None
        tickets_remaining = None
        if tiers:
            total_prizes     = sum(t.get("prizes_total") or 0 for t in tiers)
            remaining_prizes = sum(t.get("prizes_remaining") or 0 for t in tiers)
            top_tier = max(tiers, key=lambda t: t.get("prize_amount", 0), default=None)
            top_count = (top_tier or {}).get("prizes_total") or 0

            if top_prize_odds and top_count:
                total_tickets = round(top_prize_odds * top_count)
            elif overall_odds and total_prizes:
                total_tickets = round(overall_odds * total_prizes)

            if total_tickets and total_prizes > 0:
                # If total prizes exceeds total tickets the prize table is counting
                # per-play wins (multi-play game) — EV is undefined, skip it.
                if total_prizes > total_tickets:
                    total_tickets = None
                else:
                    for t in tiers:
                        if t.get("prizes_total"):
                            t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)
                    if remaining_prizes > 0:
                        tickets_remaining = round(total_tickets * remaining_prizes / total_prizes)

        return self.build_game(
            game_id=str(game_id),
            name=name,
            price=price,
            tiers=tiers,
            tickets_remaining=tickets_remaining,
            total_tickets=total_tickets,
            overall_odds=overall_odds,
            detail_url=url,
            image_url=image_url,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _extract_game_id(self, soup, page_text: str, slug: str) -> str:
        # 1. Drupal node ID from embedded JSON
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string or "")
                path = data.get("path", {}).get("currentPath", "")
                m = re.match(r"^node/(\d+)$", path)
                if m:
                    return m.group(1)
            except Exception:
                pass

        # 2. "Game No XXXX" on the page
        m = re.search(r"\bGame\s+No\s+(\d{3,6})\b", page_text, re.I)
        if m:
            return m.group(1)

        # 3. Game number embedded in image filename (DC1661...)
        m = re.search(r"/DC(\d{3,6})[^/]*\.(?:jpg|png|webp)", page_text, re.I)
        if m:
            return m.group(1)

        # 4. Slug sanitized
        return re.sub(r"[^a-z0-9]", "", slug)[:20]

    @staticmethod
    def _extract_odds(page_text: str) -> tuple[float | None, float | None]:
        """Return (overall_odds, top_prize_odds) — min and max denominators on the page.
        Handles DC's quirk of using colons as thousands separators (e.g. '1:183:420.00')."""
        vals = []
        for m in re.finditer(r"\bOdds\s+1[:/]([\d,.: ]+)", page_text, re.I):
            raw = m.group(1).strip()
            # Strip internal colons/commas/spaces, preserving the decimal point
            if "." in raw:
                int_part, dec_part = raw.rsplit(".", 1)
                int_part = re.sub(r"[,:\s]", "", int_part)
                clean = f"{int_part}.{dec_part.strip()}"
            else:
                clean = re.sub(r"[,:\s]", "", raw)
            try:
                vals.append(float(clean))
            except ValueError:
                pass
        if not vals:
            return None, None
        overall = min(vals)
        top     = max(vals)
        return (overall if overall < 100 else None), (top if top > 100 else None)
