"""
Arizona Lottery scratch-off scraper.

Second-chance: AZ Players Club (azplayersclub.com) is a JS SPA whose
promotions page renders empty server-side. No public per-game flag.
has_second_chance stays FALSE for all AZ games until a headless-friendly
2C source is wired.

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

            # Images may be JS-loaded (filenames embed the game ID,
            # e.g. "1466-instant-millions-p2.jpg"). Sniff every image
            # response — the listing card grid only renders the ~48 most
            # recently-featured games, but still-active older games appear
            # only as <a href> links and have no card image to scrape.
            # Their game-specific images DO load on the detail page itself
            # (e.g. /media/.../1440-500x-fortune-p3.jpg), so keep the sniffer
            # attached across both listing AND detail navigations and treat
            # only filenames that START with the game id as a real match
            # (avoids "001216-1440-…icon-money-bag.png" mapping to "1216").
            sniffed_img: dict[str, str] = {}

            def _capture_image(response):
                url = response.url
                low = url.lower()
                if "arizonalottery.com" not in low:
                    return
                if not any(low.endswith(ext) or (ext + "?") in low
                           for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    return
                path = url.split("?")[0]
                # Only the LAST path segment carries the game-image filename;
                # match a leading 4+ digit id so icon assets (which embed the
                # game id deeper in the path) don't outrank the real image.
                filename = path.rsplit("/", 1)[-1].lower()
                m = re.match(r"(\d{4,})[-_]", filename)
                if not m:
                    return
                gid = m.group(1)
                # Prefer the first match per id, but upgrade plain icons to a
                # proper "*-game-image.*" or "*-p<N>.*" hero when one shows up.
                existing = sniffed_img.get(gid)
                is_hero = "game-image" in filename or re.search(r"-p\d+\.", filename)
                if not existing or (is_hero and "game-image" not in existing.lower()
                                    and not re.search(r"-p\d+\.", existing.lower())):
                    sniffed_img[gid] = path

            page.on("response", _capture_image)
            try:
                slugs, game_id_to_img = self._get_slugs_and_images(page)

                # Backfill any IDs the listing didn't render a card for from
                # the sniffer captures collected so far.
                for gid, src in sniffed_img.items():
                    game_id_to_img.setdefault(gid, src)

                logger.info(
                    "AZ: %d game slugs found, %d images captured from listing "
                    "(sniffer caught %d)",
                    len(slugs), len(game_id_to_img), len(sniffed_img),
                )

                for slug in slugs:
                    url = f"{BASE_URL}/scratchers/{slug}/"
                    game_id_m = re.search(r"^(\d+)", slug)
                    game_id = game_id_m.group(1) if game_id_m else slug
                    image_url = game_id_to_img.get(game_id)
                    try:
                        game = self._scrape_detail(page, slug, url, image_url=image_url)
                        # If the listing didn't yield an image, the detail
                        # page's own assets just loaded under the sniffer —
                        # promote any matching capture into the result.
                        if game and not game.get("image_url"):
                            sniffed = sniffed_img.get(game_id)
                            if sniffed:
                                if sniffed.startswith("/"):
                                    sniffed = BASE_URL + sniffed
                                game["image_url"] = sniffed
                        if game:
                            games.append(game)
                    except Exception as e:
                        logger.debug("AZ detail error %s: %s", slug, e)
            finally:
                page.remove_listener("response", _capture_image)

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

        # ── Detail-page image fallback ───────────────────────────────────────
        # The listing-card grid only renders ~48 cards even though 78 games
        # are active, so older games arrive here with image_url=None. The
        # detail page itself has a hero ticket image inside `[class*=ticket]`
        # — pull it as a fallback before falling back further to the network
        # sniffer in the caller.
        if not image_url:
            hero = soup.select_one("[class*='ticket'] img, [class*='game-image'] img, figure img")
            if hero:
                src = hero.get("data-img-t") or hero.get("data-img-m") or hero.get("src")
                if src:
                    src = src.split("?")[0]
                    low = src.lower()
                    if (low.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
                            and "logo" not in low and "badge" not in low
                            and "icon" not in low and "starlogo" not in low):
                        if src.startswith("/"):
                            src = BASE_URL + src
                        image_url = src
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
