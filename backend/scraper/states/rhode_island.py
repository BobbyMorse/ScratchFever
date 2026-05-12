"""
Rhode Island Lottery scratch-off scraper.

Uses Playwright to load the scratchers page, which triggers an authenticated
API call to:
  https://ris.p1.awc.lotteryservices.net/api/v1/instant-games/games/?size=1000

Response fields (amounts in CENTS):
  gameId, gameName, validationStatus, ticketPrice (cents), totalTicket,
  overallOdds (string "1.56" = 1-in-1.56 chance of any win),
  prizeTiers[]: prizeAmount (cents), winningTickets (total), paidTickets (claimed)

remaining = winningTickets - paidTickets for each tier.
"""
from __future__ import annotations
import re
import logging
import threading
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

LIST_URL = "https://www.rilot.com/en-us/games/scratchers.html"
API_PATH = "/api/v1/instant-games/games/"


class RhodeIslandScraper(BaseScraper):
    state_code = "RI"
    state_name = "Rhode Island"
    base_url = "https://www.rilot.com"

    def scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("RI: playwright not installed — run: python -m playwright install chromium")
            return []

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    def _playwright_scrape(self) -> list[dict]:
        import json as _json
        from playwright.sync_api import sync_playwright

        raw_json = None

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = ctx.new_page()

            # Intercept the games API response via route — more reliable than on_response
            def handle_route(route, request):
                if API_PATH in request.url:
                    try:
                        resp = route.fetch()
                        if resp.ok and raw_json is None:
                            # Store as mutable via list trick
                            _data[0] = resp.json()
                    except Exception as e:
                        logger.debug("RI route fetch error: %s", e)
                    route.continue_()
                else:
                    route.continue_()

            _data = [None]
            page.route("**/*", handle_route)

            try:
                page.goto(LIST_URL, wait_until="networkidle", timeout=30_000)
            except Exception as e:
                logger.warning("RI: navigation error: %s", e)

            # Extra wait if data not captured yet
            if _data[0] is None:
                try:
                    page.wait_for_timeout(5_000)
                except Exception:
                    pass

            browser.close()

        if _data[0] is None:
            logger.warning("RI: no API data captured")
            return []

        raw_json = _data[0]

        if not raw_json:
            logger.warning("RI: no API data from evaluate")
            return []

        raw_games = raw_json.get("games", [])
        active = [g for g in raw_games if g.get("validationStatus") == "ACTIVE"]
        logger.info("RI: %d active games from API (of %d total)", len(active), len(raw_games))

        games = []
        for g in active:
            game = self._parse_game(g)
            if game:
                games.append(game)

        logger.info("RI: %d games parsed", len(games))
        return games

    def _parse_game(self, g: dict) -> dict | None:
        name = (g.get("gameName") or "").strip().title()
        if not name:
            return None

        game_id = str(g.get("gameId", ""))
        price_cents = g.get("ticketPrice") or 0
        price = price_cents / 100.0
        if not price:
            return None

        total_tickets = g.get("totalTicket") or None

        overall_odds_raw = g.get("overallOdds")

        # Image URL: check common field names in the RI API response
        image_url = None
        raw_img = (
            g.get("imageUrl") or g.get("image") or g.get("thumbnailUrl") or
            g.get("gameImage") or g.get("ticketImage") or g.get("img") or ""
        )
        if raw_img:
            image_url = (self.base_url + raw_img) if raw_img.startswith("/") else raw_img
        overall_odds = float(overall_odds_raw) if overall_odds_raw else None

        tiers = []
        for t in g.get("prizeTiers", []):
            prize_cents = t.get("prizeAmount") or 0
            prize = prize_cents / 100.0
            total = t.get("winningTickets") or 0
            paid = t.get("paidTickets") or 0
            remaining = max(total - paid, 0)

            if prize <= 0 or total <= 0:
                continue

            # Estimate odds per tier: totalTickets / total_prizes_in_tier
            odds_val = round(total_tickets / total, 2) if total_tickets and total > 0 else None

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      odds_val,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers:
            return None

        # EV from remaining prize value vs remaining tickets
        total_prizes = sum(t["prizes_total"] for t in tiers)
        rem_prizes   = sum(t["prizes_remaining"] for t in tiers)
        fraction_remaining = rem_prizes / total_prizes if total_prizes > 0 else None

        tickets_remaining = None
        if total_tickets and fraction_remaining is not None:
            tickets_remaining = round(total_tickets * fraction_remaining)

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            overall_odds=overall_odds,
            detail_url=f"{self.base_url}/en-us/games/scratchers.html",
            image_url=image_url,
        )
