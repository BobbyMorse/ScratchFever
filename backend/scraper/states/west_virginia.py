"""
West Virginia Lottery scratch-off scraper.

Uses Playwright (headless Chromium) because wvlottery.com is a JS SPA.
Primary: intercept XHR/fetch JSON responses the frontend loads for game data.
Fallback: parse inner_text() with regex for prize tables.

Both /games/scratch-offs/ and /games/instants/ listing pages are scraped.
"""
from __future__ import annotations
import json
import re
import logging
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

LISTING_URLS = [
    "https://www.wvlottery.com/games/scratch-offs/",
    "https://www.wvlottery.com/games/instants/",
]
BASE_URL = "https://www.wvlottery.com"

_SKIP_SLUGS = {"scratch-offs", "instants", "how-to-play", "winners", "faq",
               "prizes-remaining", "where-to-play", "news"}


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
            logger.info("WV: %d game URLs found", len(game_entries))

            seen_ids: set[str] = set()
            for url, game_id in game_entries:
                if game_id in seen_ids:
                    continue
                seen_ids.add(game_id)
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
        """Return [(url, game_id)] from both listing pages, deduped by game_id."""
        from bs4 import BeautifulSoup
        results: list[tuple[str, str]] = []
        seen: set[str] = set()

        for listing_url in LISTING_URLS:
            try:
                page.goto(listing_url, wait_until="networkidle", timeout=30_000)
            except Exception as e:
                logger.warning("WV: listing failed %s: %s", listing_url, e)
                continue

            soup = BeautifulSoup(page.content(), "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                m = re.search(
                    r"/games/(?:scratch-offs|instants)/(\d+)-([a-z0-9-]+)/?$",
                    href, re.IGNORECASE
                )
                if not m:
                    continue
                game_id = m.group(1)
                slug = m.group(2)
                if slug in _SKIP_SLUGS or game_id in seen:
                    continue
                seen.add(game_id)
                full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                results.append((full_url, game_id))

        return results

    def _scrape_detail(self, page, url: str, game_id: str) -> dict | None:
        # Intercept JSON responses the SPA fetches for this game page
        captured_json: list[dict] = []

        def _on_response(response):
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                body = response.json()
                if isinstance(body, (dict, list)):
                    captured_json.append(body)
            except Exception:
                pass

        page.on("response", _on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=25_000)
        except Exception as e:
            logger.debug("WV: goto failed %s: %s", url, e)
            page.remove_listener("response", _on_response)
            return None
        page.remove_listener("response", _on_response)

        # ── Try JSON interception first ───────────────────────────────────────
        for payload in captured_json:
            result = self._parse_json_payload(payload, game_id, url)
            if result:
                return result

        # ── Fallback: parse rendered inner_text ───────────────────────────────
        try:
            raw = page.inner_text("body")
        except Exception:
            raw = ""
        text = raw.encode("ascii", "ignore").decode()
        if len(text) < 50:
            return None

        return self._parse_text(text, game_id, url, page)

    # ── JSON payload parser (SPA API response) ────────────────────────────────

    def _parse_json_payload(self, payload, game_id: str, url: str) -> dict | None:
        """Try to extract game data from a captured JSON response."""
        # Handle list of games — find matching game_id
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id", item.get("gameId", item.get("game_id", ""))))
                if item_id == game_id:
                    return self._build_from_json_item(item, game_id, url)
            return None

        if not isinstance(payload, dict):
            return None

        # Single game object
        item_id = str(payload.get("id", payload.get("gameId", payload.get("game_id", ""))))
        if item_id == game_id or not item_id:
            return self._build_from_json_item(payload, game_id, url)

        # Nested under a key
        for key in ("game", "data", "result", "instant", "scratchOff"):
            if key in payload and isinstance(payload[key], dict):
                return self._build_from_json_item(payload[key], game_id, url)

        return None

    def _build_from_json_item(self, item: dict, game_id: str, url: str) -> dict | None:
        name = (item.get("name") or item.get("gameName") or item.get("title") or "").strip()
        if not name:
            return None

        price = None
        for k in ("price", "ticketPrice", "ticket_price", "cost"):
            v = item.get(k)
            if v is not None:
                try:
                    price = float(str(v).replace("$", "").replace(",", "").strip())
                    break
                except ValueError:
                    pass
        if not price:
            return None

        overall_odds = None
        for k in ("overallOdds", "overall_odds", "oddsOneIn", "odds"):
            v = item.get(k)
            if v is not None:
                try:
                    overall_odds = float(str(v).replace(",", ""))
                    break
                except ValueError:
                    pass

        image_url = item.get("imageUrl") or item.get("image_url") or item.get("image")

        # Parse tiers from prizes/tiers array in JSON
        tiers = []
        prize_data = item.get("prizes") or item.get("tiers") or item.get("prizeLevels") or []
        for p in prize_data:
            if not isinstance(p, dict):
                continue
            prize_amt = None
            for k in ("prize", "prizeAmount", "prize_amount", "amount", "value"):
                v = p.get(k)
                if v is not None:
                    from backend.ev_calculator import parse_prize_amount
                    prize_amt = parse_prize_amount(str(v))
                    if prize_amt:
                        break

            odds_val = None
            for k in ("odds", "oddsOneIn", "odds_one_in", "oddsIn"):
                v = p.get(k)
                if v is not None:
                    try:
                        odds_val = float(str(v).replace(",", ""))
                        break
                    except ValueError:
                        pass

            remaining = None
            for k in ("prizesRemaining", "prizes_remaining", "remaining", "unclaimed"):
                v = p.get(k)
                if v is not None:
                    try:
                        remaining = int(str(v).replace(",", ""))
                        break
                    except ValueError:
                        pass

            total = None
            for k in ("prizesTotal", "prizes_total", "total", "printed"):
                v = p.get(k)
                if v is not None:
                    try:
                        total = int(str(v).replace(",", ""))
                        break
                    except ValueError:
                        pass

            if prize_amt and prize_amt > 0:
                tiers.append({
                    "prize_amount": prize_amt,
                    "odds_one_in": odds_val,
                    "prizes_remaining": remaining,
                    "prizes_total": total,
                })

        if not tiers and not overall_odds:
            return None

        total_tickets, tickets_remaining = self._calc_ticket_counts(tiers, overall_odds)
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

    # ── Text / inner_text parser ──────────────────────────────────────────────

    def _parse_text(self, text: str, game_id: str, url: str, page) -> dict | None:
        from bs4 import BeautifulSoup

        # ── Name ─────────────────────────────────────────────────────────────
        soup = BeautifulSoup(page.content(), "lxml")
        name_el = soup.select_one(
            "h1, .game-title, [class*='game-name'], [class*='gameTitle'], [class*='gameName']"
        )
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            m = re.search(r"/\d+-([a-z0-9-]+)/?$", url, re.IGNORECASE)
            name = m.group(1).replace("-", " ").title() if m else ""
        name = re.sub(r"\s*#\d+\s*$", "", name).strip()
        if not name or len(name) < 2:
            return None

        # ── Price ─────────────────────────────────────────────────────────────
        price = None
        for pat in [
            r"(?:Ticket\s+)?Price[:\s]+\$\s*([\d.]+)",
            r"\$\s*([\d]+)\s+(?:Ticket|Scratch|Instant|Game)",
            r"Price[:\s]*\$\s*([\d.]+)",
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    v = float(m.group(1))
                    if 0 < v <= 100:
                        price = v
                        break
                except ValueError:
                    pass
        if not price:
            return None

        # ── Overall odds ──────────────────────────────────────────────────────
        overall_odds = None
        for pat in [
            r"Overall\s+Odds[:\s]+1\s+in\s+([\d,]+(?:\.\d+)?)",
            r"Odds\s+of\s+Winning[:\s]+1\s+in\s+([\d,]+(?:\.\d+)?)",
            r"1\s+in\s+([\d,]+(?:\.\d+)?)\s+overall",
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    overall_odds = float(m.group(1).replace(",", ""))
                    break
                except ValueError:
                    pass

        # ── Prize tiers ───────────────────────────────────────────────────────
        tiers = self._parse_tiers_from_text(text)

        # Also try HTML table if text parsing found nothing
        if not tiers:
            for table in soup.find_all("table"):
                tiers = self.parse_table_tiers(table)
                if tiers:
                    break

        if not tiers and not overall_odds:
            return None

        total_tickets, tickets_remaining = self._calc_ticket_counts(tiers, overall_odds)

        # Derive per-tier odds from total_tickets if missing
        if total_tickets:
            for t in tiers:
                if t.get("odds_one_in") is None and t.get("prizes_total"):
                    t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

        image_url = None
        img = soup.select_one("img[class*='game'], img[class*='ticket'], .game-image img, .ticket-image img")
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

    def _parse_tiers_from_text(self, text: str) -> list[dict]:
        """
        Try multiple line-by-line patterns for prize table rows.

        Handles common formats:
          "$1,000  1 in 300.00  45  50"   (prize, odds, remaining, total)
          "$1,000  1 in 300.00  45 of 50" (prize, odds, X of Y)
          "$1,000  300.00  45  50"        (prize, odds without "1 in", remaining, total)
        """
        tiers: list[dict] = []

        PRIZE_RE = r"\$([\d,]+(?:\.\d+)?(?:\s*(?:[Mm]illion|[Tt]housand))?)"
        ODDS_RE = r"(?:1\s+in\s+)?([\d,]+(?:\.\d+)?)"
        COUNT_RE = r"(\d[\d,]*)"

        patterns = [
            # "$prize  1 in odds  remaining of total"
            re.compile(
                PRIZE_RE + r"[\s,]+" + ODDS_RE + r"[\s,]+" + COUNT_RE + r"\s+of\s+" + COUNT_RE,
                re.IGNORECASE
            ),
            # "$prize  1 in odds  remaining  total"
            re.compile(
                PRIZE_RE + r"[\s,]+" + ODDS_RE + r"[\s,]+" + COUNT_RE + r"[\s,]+" + COUNT_RE,
                re.IGNORECASE
            ),
        ]

        for line in text.split("\n"):
            line = line.strip()
            if not line or "$" not in line:
                continue
            for pat in patterns:
                m = pat.match(line)
                if not m:
                    continue
                prize = self._parse_prize(m.group(1))
                try:
                    odds_val = float(m.group(2).replace(",", ""))
                    remaining = int(m.group(3).replace(",", ""))
                    total = int(m.group(4).replace(",", ""))
                except (ValueError, IndexError):
                    break
                if prize and prize > 0 and total > 0 and 1 < odds_val < 10_000_000:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds_val,
                        "prizes_remaining": remaining,
                        "prizes_total": total,
                    })
                break

        # If still empty, fall back to odds-only rows (no remaining/total)
        if not tiers:
            odds_only_pat = re.compile(
                PRIZE_RE + r"[\s,]+(?:1\s+in\s+)([\d,]+(?:\.\d+)?)",
                re.IGNORECASE
            )
            for line in text.split("\n"):
                line = line.strip()
                if not line or "$" not in line:
                    continue
                m = odds_only_pat.match(line)
                if not m:
                    continue
                prize = self._parse_prize(m.group(1))
                try:
                    odds_val = float(m.group(2).replace(",", ""))
                except (ValueError, IndexError):
                    continue
                if prize and prize > 0 and 1 < odds_val < 10_000_000:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds_val,
                        "prizes_remaining": None,
                        "prizes_total": None,
                    })

        return tiers

    # ── Shared helpers ────────────────────────────────────────────────────────

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

    @staticmethod
    def _calc_ticket_counts(
        tiers: list[dict], overall_odds: float | None
    ) -> tuple[int | None, int | None]:
        total_printed = sum(t.get("prizes_total") or 0 for t in tiers)
        total_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
        has_remaining = any(t.get("prizes_remaining") is not None for t in tiers)

        total_tickets = None
        tickets_remaining = None

        if overall_odds and total_printed > 0:
            total_tickets = round(overall_odds * total_printed)
            if has_remaining and total_remaining >= 0:
                tickets_remaining = round(overall_odds * total_remaining)
        elif tiers:
            estimates = [
                int(t["prizes_total"] * t["odds_one_in"])
                for t in tiers
                if t.get("prizes_total") and t.get("odds_one_in")
            ]
            if estimates:
                estimates.sort()
                total_tickets = estimates[len(estimates) // 2]
                if total_printed > 0 and has_remaining:
                    tickets_remaining = round(total_tickets * total_remaining / total_printed)

        return total_tickets, tickets_remaining
