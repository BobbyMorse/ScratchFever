"""
West Virginia Lottery scratch-off / instant-game scraper.

wvlottery.com is a Next.js SPA — game cards are client-rendered.
Strategy:
  1. Fetch sitemap.xml (static, no JS needed) to collect every game URL.
  2. Use Playwright to render each detail page (JS-rendered prize table).
  3. Parse inner_text() for price, overall odds, total tickets, prize rows.

Detail page inner_text structure:
  GAME NAME
  ...
  Overall Odds        <-- "1 in X" — X is written as plain number
  5.25
  Total Tickets in Game
  1,920,000
  ...
  Prizes
  Prize\tTotal Prizes\tRemaining Prizes
  $1\t161,188\t153,717
  $500\t21\t21
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://wvlottery.com/sitemap.xml"
LISTING_URLS = [
    "https://www.wvlottery.com/games/scratch-offs/",
    "https://www.wvlottery.com/games/instants/",
]
BASE_URL = "https://www.wvlottery.com"


class WestVirginiaScraper(BaseScraper):
    state_code = "WV"
    state_name = "West Virginia"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("WV: playwright not installed — run: python -m playwright install chromium")
            return []

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    # ── runs in a plain thread (no asyncio loop) ──────────────────────────────

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

            game_entries = self._collect_game_urls(page)
            logger.info("WV: %d game URLs collected", len(game_entries))

            for url, game_id in game_entries:
                try:
                    game = self._scrape_detail(page, url, game_id)
                    if game:
                        games.append(game)
                except Exception as e:
                    logger.debug("WV detail error %s: %s", url, e)

            browser.close()

        logger.info("WV: %d games scraped", len(games))
        return games

    def _collect_game_urls(self, page) -> list[tuple[str, str]]:
        """Return [(full_url, game_id)] for all scratch-off/instant games."""
        urls: dict[str, str] = {}

        # Primary: sitemap (no JS needed — fast)
        try:
            resp = self.get(SITEMAP_URL)
            for m in re.finditer(
                r"<loc>(https://wvlottery\.com/games/(?:scratch-offs|instants)/(\d+)-[^<]+)</loc>",
                resp.text,
            ):
                game_id = m.group(2)
                if game_id not in urls:
                    urls[game_id] = m.group(1).replace("http://", "https://").replace(
                        "wvlottery.com", "www.wvlottery.com"
                    )
        except Exception as e:
            logger.warning("WV: sitemap fetch failed: %s", e)

        # Supplement: rendered listing pages (catches games not yet in sitemap)
        for listing_url in LISTING_URLS:
            try:
                page.goto(listing_url, wait_until="networkidle", timeout=30_000)
                # Give React an extra moment to hydrate game cards
                try:
                    page.wait_for_selector(
                        'a[href*="/games/scratch-offs/"], a[href*="/games/instants/"]',
                        timeout=8_000,
                    )
                except Exception:
                    pass
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page.content(), "lxml")
                for a in soup.find_all("a", href=True):
                    m = re.search(
                        r"/games/(?:scratch-offs|instants)/(\d+)-([a-z0-9-]+)/?$",
                        a["href"], re.IGNORECASE,
                    )
                    if not m:
                        continue
                    game_id = m.group(1)
                    if game_id not in urls:
                        href = a["href"]
                        full = href if href.startswith("http") else f"{BASE_URL}{href}"
                        urls[game_id] = full
            except Exception as e:
                logger.warning("WV: listing page failed %s: %s", listing_url, e)

        return list(urls.items())  # (game_id, url) → swap below
        # Note: dict is {game_id: url}, return as [(url, game_id)]

    def _collect_game_urls(self, page) -> list[tuple[str, str]]:  # noqa: F811
        """Return [(full_url, game_id)] for all scratch-off/instant games."""
        urls: dict[str, str] = {}

        # Primary: sitemap (no JS needed — fast)
        try:
            resp = self.get(SITEMAP_URL)
            for m in re.finditer(
                r"<loc>(https://wvlottery\.com/games/(?:scratch-offs|instants)/(\d+)-[^<]+)</loc>",
                resp.text,
            ):
                game_id = m.group(2)
                if game_id not in urls:
                    urls[game_id] = m.group(1).replace("http://", "https://").replace(
                        "wvlottery.com", "www.wvlottery.com"
                    )
        except Exception as e:
            logger.warning("WV: sitemap fetch failed: %s", e)

        # Supplement: rendered listing pages (catches games not yet in sitemap)
        for listing_url in LISTING_URLS:
            try:
                page.goto(listing_url, wait_until="networkidle", timeout=30_000)
                try:
                    page.wait_for_selector(
                        'a[href*="/games/scratch-offs/"], a[href*="/games/instants/"]',
                        timeout=8_000,
                    )
                except Exception:
                    pass
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page.content(), "lxml")
                for a in soup.find_all("a", href=True):
                    m = re.search(
                        r"/games/(?:scratch-offs|instants)/(\d+)-([a-z0-9-]+)/?$",
                        a["href"], re.IGNORECASE,
                    )
                    if not m:
                        continue
                    game_id = m.group(1)
                    if game_id not in urls:
                        href = a["href"]
                        full = href if href.startswith("http") else f"{BASE_URL}{href}"
                        urls[game_id] = full
            except Exception as e:
                logger.warning("WV: listing page failed %s: %s", listing_url, e)

        return [(url, gid) for gid, url in urls.items()]

    # ── detail page ───────────────────────────────────────────────────────────

    def _scrape_detail(self, page, url: str, game_id: str) -> dict | None:
        try:
            page.goto(url, wait_until="networkidle", timeout=25_000)
        except Exception as e:
            logger.debug("WV: goto failed %s: %s", url, e)
            return None

        # Wait for prize table to appear
        try:
            page.wait_for_selector("table, [class*='prize'], [class*='Prize']", timeout=8_000)
        except Exception:
            pass

        try:
            raw = page.inner_text("body")
        except Exception:
            return None
        text = raw.encode("ascii", "ignore").decode()

        if len(text) < 100 or "Prize" not in text:
            return None

        # ── Name ─────────────────────────────────────────────────────────────
        # First non-empty line (before "Skip to Main Content")
        name = ""
        for line in text.split("\n"):
            line = line.strip()
            if line and line.lower() not in ("", "skip to main content"):
                name = line
                break
        if not name or len(name) < 2:
            return None

        # ── Price ─────────────────────────────────────────────────────────────
        price = None
        # Pattern: "Price\n$X" in the key-value block
        m = re.search(r"\bPrice\s*\n\s*\$\s*([\d.]+)", text, re.IGNORECASE)
        if m:
            try:
                price = float(m.group(1))
            except ValueError:
                pass
        if not price:
            for pat in [
                r"Ticket\s+Price[:\s]+\$\s*([\d.]+)",
                r"Price[:\s]+\$\s*([\d.]+)",
            ]:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    try:
                        price = float(m.group(1))
                        break
                    except ValueError:
                        pass
        if not price:
            return None

        # ── Overall odds ──────────────────────────────────────────────────────
        # "Overall Odds\n5.25"  (the number IS the 1-in-X value)
        overall_odds = None
        m = re.search(r"Overall\s+Odds\s*\n\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            try:
                overall_odds = float(m.group(1).replace(",", ""))
            except ValueError:
                pass
        if not overall_odds:
            m = re.search(r"Overall\s+Odds.*?1\s+in\s+([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
            if m:
                try:
                    overall_odds = float(m.group(1).replace(",", ""))
                except ValueError:
                    pass

        # ── Total tickets printed ─────────────────────────────────────────────
        total_tickets = None
        m = re.search(r"Total\s+Tickets\s+in\s+Game\s*\n\s*([\d,]+)", text, re.IGNORECASE)
        if m:
            try:
                total_tickets = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

        # ── Prize table ───────────────────────────────────────────────────────
        tiers = self._parse_prize_table(text, total_tickets)

        if not tiers and not overall_odds:
            return None

        # ── Ticket counts ─────────────────────────────────────────────────────
        tickets_remaining = None
        if total_tickets and tiers:
            total_printed = sum(t.get("prizes_total") or 0 for t in tiers)
            total_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
            if total_printed > 0 and total_remaining >= 0:
                tickets_remaining = round(total_tickets * total_remaining / total_printed)
        elif overall_odds and tiers:
            # Derive total_tickets from odds × sum-of-all-prizes
            total_printed = sum(t.get("prizes_total") or 0 for t in tiers)
            total_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
            if total_printed > 0:
                total_tickets = round(overall_odds * total_printed)
                if total_remaining >= 0:
                    tickets_remaining = round(total_tickets * total_remaining / total_printed)

        # Derive per-tier odds from total_tickets
        if total_tickets:
            for t in tiers:
                if t.get("odds_one_in") is None and t.get("prizes_total"):
                    t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

        # ── Image ─────────────────────────────────────────────────────────────
        image_url = None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page.content(), "lxml")
        img = soup.select_one(
            "img[class*='game'], img[class*='ticket'], img[alt*='scratch'], "
            ".game-image img, main img"
        )
        if img and img.get("src"):
            src = img["src"]
            image_url = src if src.startswith("http") else f"{BASE_URL}{src}"

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

    def _parse_prize_table(self, text: str, total_tickets: int | None) -> list[dict]:
        """
        Parse the tab-separated prize table from inner_text.

        Expected format (after JS rendering):
          Prizes
          Prize\tTotal Prizes\tRemaining Prizes
          $1\t161,188\t153,717
          $500\t21\t21
        """
        tiers = []

        # Find the prize section
        prize_section_m = re.search(
            r"(?:Prizes?\s*\n)Prize\s*\t\s*Total\s+Prizes?\s*\t\s*Remaining\s+Prizes?(.+?)(?:\n\n|\Z)",
            text, re.DOTALL | re.IGNORECASE,
        )

        if prize_section_m:
            section = prize_section_m.group(1)
            for line in section.split("\n"):
                line = line.strip()
                if not line or "$" not in line:
                    continue
                parts = [p.strip() for p in line.split("\t")]
                if len(parts) < 2:
                    continue
                prize = self._parse_prize(parts[0])
                if not prize:
                    continue
                total = remaining = None
                try:
                    total = int(parts[1].replace(",", "")) if len(parts) > 1 else None
                except (ValueError, IndexError):
                    pass
                try:
                    remaining = int(parts[2].replace(",", "")) if len(parts) > 2 else None
                except (ValueError, IndexError):
                    pass
                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": None,  # filled later from total_tickets
                    "prizes_total": total,
                    "prizes_remaining": remaining,
                })
        else:
            # Fallback: scan all lines for "$X  Y  Z" tab or whitespace-separated rows
            in_prizes = False
            for line in text.split("\n"):
                stripped = line.strip()
                if re.search(r"^Prize\s", stripped, re.IGNORECASE):
                    in_prizes = True
                    continue
                if not in_prizes:
                    continue
                if not stripped or "$" not in stripped:
                    if in_prizes and stripped and "$" not in stripped and not stripped[0].isdigit():
                        break
                    continue

                parts = [p.strip() for p in re.split(r"\t|  +", stripped)]
                if len(parts) < 2:
                    continue
                prize = self._parse_prize(parts[0])
                if not prize:
                    continue
                total = remaining = None
                try:
                    total = int(parts[1].replace(",", ""))
                except (ValueError, IndexError):
                    pass
                try:
                    remaining = int(parts[2].replace(",", "")) if len(parts) > 2 else None
                except (ValueError, IndexError):
                    pass
                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": None,
                    "prizes_total": total,
                    "prizes_remaining": remaining,
                })

        return tiers

    @staticmethod
    def _parse_prize(txt: str) -> float | None:
        txt = txt.strip().lower().replace(",", "")
        m = re.match(r"\$?([\d.]+)\s*(million|thousand)?", txt)
        if not m:
            return None
        try:
            val = float(m.group(1))
        except ValueError:
            return None
        if m.group(2) == "million":
            val *= 1_000_000
        elif m.group(2) == "thousand":
            val *= 1_000
        return val if val > 0 else None
