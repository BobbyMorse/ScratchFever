"""
Arizona Lottery scratch-off scraper.

Uses Playwright (headless Chromium) to bypass Cloudflare WAF.

Prize tables are JS-rendered and not present in raw HTML;
we parse them from page.inner_text() where each row looks like:
  $5 Million   1,329,106   3 of 4
  (prize)      (odds 1-in)  (remaining of total)
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

LISTING_URLS = [
    "https://www.arizonalottery.com/scratchers/",
    "https://www.arizonalottery.com/scratchers/top-prizes-remaining/",
]
BASE_URL = "https://www.arizonalottery.com"


class ArizonaScraper(BaseScraper):
    state_code = "AZ"
    state_name = "Arizona"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("AZ: playwright not installed — run: python -m playwright install chromium")
            return []

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    # ── runs in a plain thread (no asyncio loop) ─────────────────────────────

    def _playwright_scrape(self) -> list[dict]:
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

            # Listing tiles are server-rendered: each game lives in a
            # <div class="card" data-game-id="1523"> with a thumbnail <img>.
            slugs, game_id_to_img = self._get_slugs_and_images(page)

            logger.info("AZ: %d game slugs found, %d images captured", len(slugs), len(game_id_to_img))

            for slug in slugs:
                url = f"{BASE_URL}/scratchers/{slug}/"
                game_id_m = re.search(r"^(\d+)", slug)
                game_id = game_id_m.group(1) if game_id_m else slug
                image_url = game_id_to_img.get(game_id)
                try:
                    game = self._scrape_detail(page, slug, url, image_url=image_url)
                    if game:
                        games.append(game)
                except Exception as e:
                    logger.debug("AZ detail error %s: %s", slug, e)

            browser.close()

        logger.info("AZ: %d games scraped", len(games))
        return games

    def _get_slugs_and_images(self, page) -> tuple[list[str], dict[str, str]]:
        """Collect slugs and per-game thumbnail URLs from listing pages."""
        from bs4 import BeautifulSoup

        _SKIP = {"top-prizes-remaining", "how-to-play", "winners",
                 "scratchers", "faq", "instant-tabs", "remaining-prizes"}

        seen: set[str] = set()
        slugs: list[str] = []
        game_id_to_img: dict[str, str] = {}

        for url in LISTING_URLS:
            try:
                page.goto(url, wait_until="networkidle", timeout=30_000)
            except Exception as e:
                logger.warning("AZ: listing page failed %s: %s", url, e)
                continue
            soup = BeautifulSoup(page.content(), "lxml")

            for card in soup.select("div.card[data-game-id]"):
                gid = (card.get("data-game-id") or "").strip()
                if not gid or gid in game_id_to_img:
                    continue
                img = card.find("img")
                if not img:
                    continue
                # Prefer the larger thumbnail (data-img-t) when present.
                src = img.get("data-img-t") or img.get("data-img-m") or img.get("src")
                if not src:
                    continue
                src = src.split("?")[0]
                if src.startswith("/"):
                    src = BASE_URL + src
                game_id_to_img[gid] = src

            for a in soup.find_all("a", href=True):
                m = re.search(r"/scratchers/([a-z0-9-]+)/?$", a["href"])
                if not m:
                    continue
                slug = m.group(1)
                if slug in _SKIP or slug in seen:
                    continue
                seen.add(slug)
                slugs.append(slug)

        return slugs, game_id_to_img

    def _scrape_detail(self, page, slug: str, url: str, image_url: str | None = None) -> dict | None:
        page.goto(url, wait_until="networkidle", timeout=20_000)

        # inner_text gives the JS-rendered text including dynamic prize table
        try:
            raw = page.inner_text("body")
        except Exception:
            raw = ""
        text = raw.encode("ascii", "ignore").decode()

        if "Ticket Price" not in text and "PRIZES AND ODDS" not in text:
            return None

        # ── Name ───────────────────────────────────────────────────────────────
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page.content(), "lxml")
        name_el = soup.select_one("h1, .game-title, [class*='game-name']")
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            # Fall back to page title
            m = re.search(r"^(.+?)(?:\s*#\d+)?$", text.split("\n")[0].strip())
            name = m.group(1).strip() if m else slug.replace("-", " ").title()
        name = re.sub(r"\s*#\d+\s*$", "", name).strip()
        if not name or len(name) < 2:
            return None

        # ── Game ID (numeric portion of slug) ─────────────────────────────────
        game_id_m = re.search(r"^(\d+)", slug)
        game_id = game_id_m.group(1) if game_id_m else slug

        # ── Price ─────────────────────────────────────────────────────────────
        price = None
        for pat in [
            r"Ticket\s+Price[:\s]+\$?([\d.]+)",
            r"Price[:\s]+\$?([\d.]+)",
            r"\$(\d+)\s+Scratcher",
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                price = float(m.group(1))
                break
        if not price:
            return None

        # ── Overall odds ───────────────────────────────────────────────────────
        overall_odds = None
        odds_m = re.search(r"Overall\s+Odds[:\s]+1\s+in\s+([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if odds_m:
            overall_odds = float(odds_m.group(1).replace(",", ""))

        # ── Prize table — parse "$amount   odds   X of Y" rows ───────────────
        tiers = []
        # Find the prizes section between "PRIZES AND ODDS" and "HOW TO PLAY"
        prizes_section = re.search(
            r"PRIZES AND ODDS(.+?)(?:HOW TO PLAY|FEATURED WINNER|$)",
            text, re.DOTALL | re.IGNORECASE
        )
        section_text = prizes_section.group(1) if prizes_section else text

        for line in section_text.split("\n"):
            line = line.strip()
            # Matches: "$5 Million   1,329,106   3 of 4"
            #      or: "$500,000   531,642.6   9 of 10"
            m = re.match(
                r"\$([\d,]+(?:\.\d+)?(?:\s*(?:Million|Thousand))?)"
                r"\s+([\d,]+(?:\.\d+)?)"
                r"\s+(\d[\d,]*)\s+of\s+(\d[\d,]*)",
                line, re.IGNORECASE
            )
            if not m:
                continue
            prize = self._parse_prize(m.group(1))
            odds_val = float(m.group(2).replace(",", ""))
            remaining = int(m.group(3).replace(",", ""))
            total = int(m.group(4).replace(",", ""))
            if prize and prize > 0 and total > 0:
                tiers.append({
                    "prize_amount":     prize,
                    "odds_one_in":      odds_val,
                    "prizes_remaining": remaining,
                    "prizes_total":     total,
                })

        if not tiers and not overall_odds:
            return None

        total_tickets = None
        tickets_remaining = None
        if tiers:
            total_prizes_printed   = sum(t["prizes_total"] for t in tiers)
            total_prizes_remaining = sum(t["prizes_remaining"] for t in tiers)
            if overall_odds and total_prizes_printed > 0:
                total_tickets     = round(overall_odds * total_prizes_printed)
                tickets_remaining = round(overall_odds * total_prizes_remaining)
            else:
                refs = [(t["prizes_total"], t["odds_one_in"]) for t in tiers
                        if t["prizes_total"] > 0 and t["odds_one_in"]]
                if refs:
                    estimates = sorted(int(tot * odds) for tot, odds in refs)
                    total_tickets = estimates[len(estimates) // 2]
                    if total_prizes_printed > 0:
                        tickets_remaining = round(total_tickets * total_prizes_remaining / total_prizes_printed)

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
