"""
South Dakota Lottery scratch-off scraper.

Game list via WordPress REST API (custom post type "game"):
  GET https://lottery.sd.gov/wp-json/wp/v2/game
    ?game_option=24  (Active)
    &game_type=3     (Scratch)
    &per_page=100
  Returns id, slug, title, game_price[]

game_price taxonomy → dollar amounts:
  {41: $1, 40: $2, 42: $3, 43: $5, 44: $10, 45: $20, 49: $30}

Detail pages: https://lottery.sd.gov/game/{slug}/
  Prize table is JS-rendered via IGT's platform.
  Uses Playwright; tries HTML table parse first, falls back to inner_text regex.
"""
from __future__ import annotations
import html as html_mod
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

GAME_API = "https://lottery.sd.gov/wp-json/wp/v2/game"
BASE_URL = "https://lottery.sd.gov"

# game_price taxonomy term ID → ticket price
PRICE_MAP: dict[int, float] = {
    41: 1.0, 40: 2.0, 42: 3.0, 43: 5.0, 44: 10.0, 45: 20.0, 49: 30.0
}


class SouthDakotaScraper(BaseScraper):
    state_code = "SD"
    state_name = "South Dakota"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("SD: playwright not installed")
            return []

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    def _playwright_scrape(self) -> list[dict]:
        games_meta = self._fetch_game_list()
        logger.info("SD: %d active scratch games from WP API", len(games_meta))

        from playwright.sync_api import sync_playwright
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
                locale="en-US",
            )
            page = ctx.new_page()

            for meta in games_meta:
                slug = meta["slug"]
                url = f"{BASE_URL}/game/{slug}/"
                try:
                    game = self._scrape_detail(page, meta, url)
                    if game:
                        games.append(game)
                except Exception as e:
                    logger.debug("SD detail error %s: %s", slug, e)

            browser.close()

        logger.info("SD: %d games scraped", len(games))
        return games

    def _fetch_game_list(self) -> list[dict]:
        """Fetch all active scratch games via paginated WP REST API.

        Single per_page=100 calls have intermittently returned partial results
        on Railway (~19/49). Paginate explicitly and validate against the
        X-WP-Total header so a truncated response surfaces as a hard error
        instead of a silently-shrunken game list.
        """
        params = {
            "per_page": 50,
            "game_option": 24,  # Active
            "game_type": 3,     # Scratch
            "_fields": "id,slug,title,game_price",
        }
        all_games: list[dict] = []
        page = 1
        expected_total: int | None = None
        while True:
            resp = self.get(GAME_API, params={**params, "page": page})
            if expected_total is None:
                try:
                    expected_total = int(resp.headers.get("X-WP-Total", "0"))
                except (TypeError, ValueError):
                    expected_total = None
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            all_games.extend(batch)
            if len(batch) < params["per_page"]:
                break
            page += 1
            if page > 20:  # safety guard
                break

        if expected_total and len(all_games) < expected_total:
            raise RuntimeError(
                f"SD WP API returned {len(all_games)}/{expected_total} games — "
                f"truncated response"
            )
        return all_games

    def _scrape_detail(self, page, meta: dict, url: str) -> dict | None:
        slug = meta["slug"]
        name = html_mod.unescape(meta["title"]["rendered"])
        price_ids = meta.get("game_price", [])
        price = PRICE_MAP.get(price_ids[0]) if price_ids else None
        if not price:
            return None

        page.goto(url, wait_until="networkidle", timeout=25_000)

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page.content(), "lxml")

        # Try HTML table parse first
        tiers: list[dict] = []
        for table in soup.find_all("table"):
            tiers = self.parse_table_tiers(table)
            if tiers:
                break

        # Fall back to inner_text line parsing
        if not tiers:
            try:
                raw = page.inner_text("body")
            except Exception:
                raw = ""
            tiers = _parse_text_tiers(raw)

        if not tiers:
            logger.debug("SD: no prize tiers for %s", slug)
            return None

        page_text = soup.get_text(" ", strip=True)
        overall_odds = _extract_overall_odds(page_text)

        total_prizes_printed = sum(t.get("prizes_total") or 0 for t in tiers)
        total_prizes_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)

        total_tickets = None
        tickets_remaining = None
        if overall_odds and total_prizes_printed > 0:
            total_tickets = round(overall_odds * total_prizes_printed)
            tickets_remaining = round(overall_odds * total_prizes_remaining)
        else:
            refs = [
                (t["prizes_total"], t["odds_one_in"]) for t in tiers
                if t.get("prizes_total") and t.get("odds_one_in")
            ]
            if refs:
                estimates = sorted(int(tot * odds) for tot, odds in refs)
                total_tickets = estimates[len(estimates) // 2]
                if total_prizes_printed > 0:
                    tickets_remaining = round(
                        total_tickets * total_prizes_remaining / total_prizes_printed
                    )

        return self.build_game(
            game_id=str(meta['id']),
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=url,
        )


def _extract_overall_odds(text: str) -> float | None:
    m = re.search(
        r"(?:overall\s+odds?|odds?\s+of\s+winning\s+any\s+prize)[:\s]+1\s+in\s+([\d,]+(?:\.\d+)?)",
        text, re.I,
    )
    return float(m.group(1).replace(",", "")) if m else None


def _parse_text_tiers(text: str) -> list[dict]:
    """Parse prize tiers from Playwright inner_text when no HTML table is found."""
    text = text.encode("ascii", "ignore").decode()
    tiers = []

    section_m = re.search(
        r"(?:PRIZES?\s+(?:AND\s+)?ODDS?|PRIZE\s+TABLE|CHANCES\s+OF\s+WINNING)(.+?)"
        r"(?:HOW\s+TO\s+PLAY|ABOUT\s+THIS\s+GAME|$)",
        text, re.DOTALL | re.IGNORECASE,
    )
    section = section_m.group(1) if section_m else text

    for line in section.split("\n"):
        line = line.strip()
        if not line or not line.startswith("$"):
            continue

        # "$5.00   7.14   1,400,000   875,000"  →  prize, odds, total, remaining
        m = re.match(
            r"\$([\d,]+(?:\.\d+)?(?:\s+(?:Million|Thousand))?)"
            r"\s+([\d,]+(?:\.\d+)?)"
            r"\s+([\d,]+)"
            r"\s+([\d,]+)",
            line, re.I,
        )
        if m:
            prize = parse_prize_amount("$" + m.group(1))
            odds = float(m.group(2).replace(",", ""))
            total = int(m.group(3).replace(",", ""))
            remaining = int(m.group(4).replace(",", ""))
            if prize and prize > 0 and total > 0:
                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": odds,
                    "prizes_total": total,
                    "prizes_remaining": remaining,
                })
            continue

        # "$5   875,000 of 1,400,000"  →  prize, remaining of total
        m = re.match(
            r"\$([\d,]+(?:\.\d+)?(?:\s+(?:Million|Thousand))?)"
            r".*?(\d[\d,]*)\s+of\s+(\d[\d,]*)",
            line, re.I,
        )
        if m:
            prize = parse_prize_amount("$" + m.group(1))
            remaining = int(m.group(2).replace(",", ""))
            total = int(m.group(3).replace(",", ""))
            if prize and prize > 0 and total > 0:
                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": None,
                    "prizes_total": total,
                    "prizes_remaining": remaining,
                })

    return tiers
