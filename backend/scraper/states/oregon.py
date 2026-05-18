"""
Oregon Lottery scratch-off scraper.
Uses Playwright to render JS-loaded prize/odds tables.
Listing: /scratch-its/list/  → table: Game #, Name, Price, Top Prize, Unclaimed, % Sold
Detail:  /scratch-its/{slug}/ → full prize tier table with odds and remaining counts
"""
from __future__ import annotations
import re
import logging
import concurrent.futures
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://www.oregonlottery.org"
LISTING_URL = f"{BASE_URL}/scratch-its/list/"

_SKIP_SLUGS = {
    "list", "grid", "how-to-play", "scratch-its", "winners", "faq",
    "second-chance", "responsible-gambling", "winning-numbers", "prize-claim",
}


class OregonScraper(BaseScraper):
    state_code = "OR"
    state_name = "Oregon"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("OR: playwright not installed — run: python -m playwright install chromium")
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    def _playwright_scrape(self) -> list[dict]:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        games = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()

            # --- Listing page: extract game slugs (and game numbers if available) ---
            slug_to_game_num: dict[str, str] = {}
            try:
                page.goto(LISTING_URL, wait_until="networkidle", timeout=35_000)
                # Wait for the table rows to populate
                try:
                    page.wait_for_selector("table tbody tr td", timeout=15_000)
                except Exception:
                    pass
                soup = BeautifulSoup(page.content(), "lxml")
                slug_to_game_num = self._parse_listing(soup)
            except Exception as e:
                logger.warning("OR: listing page failed: %s", e)

            slugs = list(slug_to_game_num.keys())
            logger.info("OR: %d game slugs found", len(slugs))

            if not slugs:
                browser.close()
                return []

            # --- Detail pages ---
            for slug in slugs:
                url = f"{BASE_URL}/scratch-its/{slug}/"
                game_num = slug_to_game_num.get(slug)
                try:
                    game = self._scrape_detail(page, slug, url, game_num)
                    if game:
                        games.append(game)
                except Exception as e:
                    logger.debug("OR detail error %s: %s", slug, e)

            browser.close()

        logger.info("OR: %d games scraped", len(games))
        return games

    def _parse_listing(self, soup) -> dict[str, str]:
        """Extract slug → game_number mapping from the listing table."""
        slug_to_num: dict[str, str] = {}
        seen: set[str] = set()

        # Prefer table rows (Game #, Name, ...) for the game number
        rows = soup.select("table tbody tr")
        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue
            game_num = cells[0].get_text(strip=True) if cells else ""
            # Find the link in the row
            a = row.find("a", href=True)
            if not a:
                continue
            m = re.search(r"/scratch-its/([a-z0-9][a-z0-9-]+)/?$", a["href"])
            if not m:
                continue
            slug = m.group(1)
            if slug in _SKIP_SLUGS or slug in seen:
                continue
            seen.add(slug)
            slug_to_num[slug] = game_num if re.match(r"^\d+$", game_num) else ""

        # Fallback: collect all /scratch-its/{slug}/ links if table was empty
        if not slug_to_num:
            for a in soup.find_all("a", href=True):
                m = re.search(r"/scratch-its/([a-z0-9][a-z0-9-]+)/?$", a["href"])
                if not m:
                    continue
                slug = m.group(1)
                if slug in _SKIP_SLUGS or slug in seen:
                    continue
                seen.add(slug)
                slug_to_num[slug] = ""

        return slug_to_num

    def _scrape_detail(self, page, slug: str, url: str, game_num: str | None) -> dict | None:
        from bs4 import BeautifulSoup

        page.goto(url, wait_until="networkidle", timeout=25_000)
        # Wait for odds table to populate
        try:
            page.wait_for_selector("table", timeout=10_000)
        except Exception:
            pass

        soup = BeautifulSoup(page.content(), "lxml")
        raw = page.inner_text("body")
        text = raw.encode("ascii", "ignore").decode()

        # --- Name ---
        name_el = soup.select_one("h1")
        name = name_el.get_text(strip=True) if name_el else slug.replace("-", " ").title()
        name = re.sub(r"\s*#\s*\d+\s*$", "", name).strip()
        if not name or len(name) < 2:
            return None

        # --- Game ID ---
        # Use game number from listing table; fall back to extracting from page text
        if not game_num:
            m = re.search(r"Game\s*#?\s*:?\s*(\d+)", text, re.IGNORECASE)
            game_num = m.group(1) if m else ""
        game_id = f"or{game_num}" if game_num else f"or-{slug}"

        # --- Price ---
        price = None
        for pat in [
            r"Ticket\s+Cost[:\s]+\$?([\d.]+)",
            r"Price[:\s]+\$?([\d.]+)",
            r"\$(\d+(?:\.\d+)?)\s+Scratch",
            r"costs?\s+\$?([\d.]+)",
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                price = float(m.group(1))
                break
        if not price:
            return None

        # --- Overall odds ---
        # Label and value are on separate lines on Oregon's site
        overall_odds = None
        m = re.search(r"Overall\s+Odds[\s\n:]+1\s+in\s+([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            overall_odds = float(m.group(1).replace(",", ""))

        # --- Prize tiers: Oregon uses div.ol-odds-payouts-scratchits__results ---
        tiers = self._parse_div_tiers(soup)

        # Fallback: try HTML tables
        if not tiers:
            for table in soup.find_all("table"):
                parsed = self.parse_table_tiers(table)
                if len(parsed) >= 2:
                    tiers = parsed
                    break

        # Last resort: scan text lines for "$X  1 in Y  Z of W" pattern
        if not tiers:
            tiers = self._parse_text_tiers(text)

        if not tiers and not overall_odds:
            return None

        # --- Tickets total / remaining ---
        total_tickets = None
        tickets_remaining = None
        if tiers:
            total_printed = sum(t.get("prizes_total") or 0 for t in tiers)
            total_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
            if overall_odds and total_printed > 0:
                total_tickets = round(overall_odds * total_printed)
                tickets_remaining = round(overall_odds * total_remaining)
            else:
                refs = [
                    (t["prizes_total"], t["odds_one_in"]) for t in tiers
                    if t.get("prizes_total") and t.get("odds_one_in")
                ]
                if refs:
                    estimates = sorted(int(tot * odds) for tot, odds in refs)
                    total_tickets = estimates[len(estimates) // 2]
                    if total_printed > 0:
                        tickets_remaining = round(
                            total_tickets * total_remaining / total_printed
                        )

        # --- Image ---
        image_url = None
        for selector in [
            "img[src*='scratch']",
            "img[src*='game']",
            ".scratch-game img",
            "article img",
            ".game-detail img",
            "main img",
        ]:
            img = soup.select_one(selector)
            if img and img.get("src"):
                src = img["src"]
                image_url = (BASE_URL + src) if src.startswith("/") else src
                break

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=url,
            image_url=image_url,
        )

    @staticmethod
    def _parse_text_tiers(text: str) -> list[dict]:
        """Parse prize tiers from inner_text lines: '$X  1 in Y  Z of W'."""
        tiers = []
        for line in text.split("\n"):
            line = line.strip()
            m = re.match(
                r"\$([\d,]+(?:\.\d+)?(?:\s*(?:Million|Thousand))?)"
                r".*?1\s+in\s+([\d,]+(?:\.\d+)?)"
                r".*?(\d[\d,]*)\s+of\s+(\d[\d,]*)",
                line, re.IGNORECASE,
            )
            if not m:
                continue
            prize = OregonScraper._parse_prize(m.group(1))
            odds_val = float(m.group(2).replace(",", ""))
            remaining = int(m.group(3).replace(",", ""))
            total = int(m.group(4).replace(",", ""))
            if prize and prize > 0 and total > 0:
                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": odds_val,
                    "prizes_remaining": remaining,
                    "prizes_total": total,
                })
        return tiers

    @staticmethod
    def _parse_prize(txt: str) -> float | None:
        txt = txt.strip().lower().replace(",", "")
        m = re.match(r"([\d.]+)\s*(million|thousand)?", txt)
        if not m:
            return None
        val = float(m.group(1))
        if m.group(2) == "million":
            val *= 1_000_000
        elif m.group(2) == "thousand":
            val *= 1_000
        return val
