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
from datetime import datetime, timezone
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

LIST_URL = "https://www.rilot.com/en-us/games/scratchers.html"
API_PATH = "/api/v1/instant-games/games/"


class RhodeIslandScraper(BaseScraper):
    state_code = "RI"
    state_name = "Rhode Island"
    base_url = "https://www.rilot.com"
    scraper_timeout = 600

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

        _data = [None]

        def handle_route(route, request):
            if API_PATH in request.url:
                try:
                    resp = route.fetch()
                    if resp.ok and _data[0] is None:
                        _data[0] = resp.json()
                except Exception as e:
                    logger.debug("RI route fetch error: %s", e)
                route.continue_()
            else:
                route.continue_()

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

            page.route("**/*", handle_route)

            try:
                page.goto(LIST_URL, wait_until="networkidle", timeout=30_000)
            except Exception as e:
                logger.warning("RI: navigation error: %s", e)

            if _data[0] is None:
                try:
                    page.wait_for_timeout(5_000)
                except Exception:
                    pass

            browser.close()

        if _data[0] is None:
            logger.warning("RI: no API data captured")
            return []

        import time as _time
        raw_games = _data[0].get("games", [])
        now_ms = _time.time() * 1000
        active = [
            g for g in raw_games
            if g.get("validationStatus") == "ACTIVE"
            and (g.get("endDistributionDate") or 0) > now_ms
        ]
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

        # The API's `overallOdds` field is not the cash-prize overall odds shown
        # on rilot.com (it returns values like 1.33 that imply >75% win rate).
        # Compute it ourselves from the prize-tier winning-ticket counts.

        end_date = None
        end_ms = g.get("endDistributionDate")
        if end_ms:
            end_date = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date().isoformat()

        # RI serves ticket thumbnails from a deterministic CDN path keyed by game ID.
        image_url = None
        if game_id:
            image_url = f"{self.base_url}/content/dam/interactive/ilottery/images/instantgames/{game_id}/thumb-sq.jpg"
        # Fall back to any API-provided field, in case the CDN convention changes.
        if not image_url:
            raw_img = (
                g.get("ticketImageUrl") or g.get("imageUrl") or g.get("image") or
                g.get("thumbnailUrl") or g.get("gameImage") or g.get("ticketImage") or
                g.get("img") or ""
            )
            if raw_img:
                image_url = (self.base_url + raw_img) if raw_img.startswith("/") else raw_img

        tiers = []
        for t in g.get("prizeTiers", []):
            prize_cents = t.get("prizeAmount") or 0
            prize = prize_cents / 100.0
            total = t.get("winningTickets") or 0
            paid = t.get("paidTickets") or 0
            remaining = max(total - paid, 0)

            if prize <= 0 or total <= 0:
                continue

            odds_val = round(total_tickets / total, 2) if total_tickets and total > 0 else None

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      odds_val,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers:
            return None

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
            end_date=end_date,
        )
