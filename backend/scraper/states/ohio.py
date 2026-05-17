"""
Ohio Lottery scratch-off scraper.
Prizes-remaining page: https://www.ohiolottery.com/games/scratch-offs/prizes-remaining

The page is a Vue.js SPA backed by api-solutions.ohiolottery.com. Previous attempts
to call that API directly failed because the Bearer-token endpoint blocks Python clients.
Playwright lets the SPA's own JS handle auth; we intercept the authenticated JSON
responses instead of calling the auth endpoint ourselves.

Data available per game: name, game number, price, prize tiers with prizes_remaining,
prizes_total, odds. Updated daily at ~6am ET.
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

PRIZES_URL = "https://www.ohiolottery.com/games/scratch-offs/prizes-remaining"
BASE_URL = "https://www.ohiolottery.com"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class OhioScraper(BaseScraper):
    state_code = "OH"
    state_name = "Ohio"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("OH: playwright not installed — run: python -m playwright install chromium")
            return []

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    # ── runs in a plain thread (sync_playwright needs no event loop) ──────────

    def _playwright_scrape(self) -> list[dict]:
        from playwright.sync_api import sync_playwright

        captured: list[tuple[str, object]] = []  # (url, parsed_json)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=_UA,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = ctx.new_page()

            def _on_response(resp):
                try:
                    if "ohiolottery.com" not in resp.url.lower():
                        return
                    if "json" not in resp.headers.get("content-type", ""):
                        return
                    if resp.status != 200:
                        return
                    data = resp.json()
                    captured.append((resp.url, data))
                    logger.debug("OH: JSON captured from %s", resp.url)
                except Exception:
                    pass

            page.on("response", _on_response)

            try:
                page.goto(PRIZES_URL, wait_until="networkidle", timeout=45_000)
                page.wait_for_timeout(3_000)
            except Exception as e:
                logger.warning("OH: page load error: %s", e)

            rendered_html = ""
            try:
                rendered_html = page.content()
            except Exception:
                pass

            page.close()
            browser.close()

        raw_games = self._find_games_in_responses(captured)

        if raw_games:
            logger.info("OH: %d raw game records from API", len(raw_games))
            games = [g for g in (self._parse_game(r) for r in raw_games) if g]
            logger.info("OH: %d games parsed", len(games))
            return games

        if rendered_html:
            logger.info("OH: falling back to rendered HTML parse")
            games = self._parse_html(rendered_html)
            logger.info("OH: %d games from HTML fallback", len(games))
            return games

        logger.warning("OH: no game data found — API interception returned nothing")
        return []

    # ── API response hunting ──────────────────────────────────────────────────

    def _find_games_in_responses(self, responses: list[tuple[str, object]]) -> list[dict]:
        best: list[dict] = []
        for url, data in responses:
            candidates = self._extract_game_list(data)
            if len(candidates) > len(best):
                best = candidates
                logger.info("OH: best candidate now %d games from %s", len(best), url)
        return best

    def _extract_game_list(self, data: object) -> list[dict]:
        if isinstance(data, list) and data and isinstance(data[0], dict):
            if self._looks_like_game(data[0]):
                return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for key in (
                "games", "data", "results", "scratchers", "items",
                "scratchOffs", "ScratchOffs", "Games", "ScratchGames",
            ):
                val = data.get(key)
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    if self._looks_like_game(val[0]):
                        return [d for d in val if isinstance(d, dict)]
        return []

    @staticmethod
    def _looks_like_game(d: dict) -> bool:
        keys = {k.lower() for k in d}
        has_name = bool(keys & {"gamename", "game_name", "name", "title"})
        has_id = bool(keys & {"gameid", "game_id", "gamenumber", "game_number", "gamenm", "id"})
        has_price = bool(keys & {"price", "ticketprice", "ticket_price", "cost"})
        return has_name and (has_id or has_price)

    # ── Game parsing ──────────────────────────────────────────────────────────

    def _parse_game(self, g: dict) -> dict | None:
        name = (
            g.get("GameName") or g.get("gameName") or g.get("game_name") or
            g.get("Name") or g.get("name") or g.get("Title") or g.get("title") or ""
        ).strip()
        if not name:
            return None

        game_id_raw = str(
            g.get("GameNumber") or g.get("gameNumber") or g.get("game_number") or
            g.get("GameId") or g.get("gameId") or g.get("game_id") or
            g.get("GameNum") or g.get("Id") or g.get("id") or ""
        ).strip()
        if not game_id_raw:
            return None

        price_raw = (
            g.get("TicketPrice") or g.get("ticketPrice") or g.get("ticket_price") or
            g.get("Price") or g.get("price") or g.get("Cost") or g.get("cost") or 0
        )
        price = parse_prize_amount(str(price_raw))
        if not price:
            return None

        overall_odds_raw = (
            g.get("OverallOdds") or g.get("overallOdds") or g.get("overall_odds") or
            g.get("Odds") or g.get("odds") or ""
        )
        overall_odds = parse_odds(str(overall_odds_raw)) if overall_odds_raw else None

        tiers_raw = (
            g.get("PrizeTiers") or g.get("prizeTiers") or g.get("prize_tiers") or
            g.get("Prizes") or g.get("prizes") or g.get("Tiers") or g.get("tiers") or []
        )
        tiers = self._parse_tiers(tiers_raw)
        if not tiers:
            return None

        total_tickets, tickets_remaining = self._estimate_tickets(tiers, overall_odds)

        image_url = (
            g.get("ImageUrl") or g.get("imageUrl") or g.get("image_url") or
            g.get("Image") or g.get("image") or g.get("LogoUrl") or g.get("logoUrl") or
            g.get("ThumbnailUrl") or g.get("thumbnailUrl") or None
        )
        if image_url and isinstance(image_url, str) and image_url.startswith("/"):
            image_url = BASE_URL + image_url

        end_date = (g.get("EndDate") or g.get("endDate") or g.get("end_date") or "")
        end_date = str(end_date)[:10] if end_date else None

        return self.build_game(
            game_id=f"oh{game_id_raw}",
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=f"{BASE_URL}/games/scratch-offs/{game_id_raw}",
            image_url=image_url,
            end_date=end_date,
        )

    def _parse_tiers(self, tiers_raw: list) -> list[dict]:
        tiers = []
        for t in (tiers_raw or []):
            if not isinstance(t, dict):
                continue
            prize = parse_prize_amount(str(
                t.get("PrizeAmount") or t.get("prizeAmount") or t.get("prize_amount") or
                t.get("Prize") or t.get("prize") or t.get("Amount") or t.get("amount") or 0
            ))
            if not prize or prize <= 0:
                continue

            odds_raw = (
                t.get("Odds") or t.get("odds") or t.get("OddsOneIn") or t.get("oddsOneIn") or
                t.get("WinningOdds") or t.get("winningOdds") or t.get("winning_odds") or ""
            )
            odds = parse_odds(str(odds_raw)) if odds_raw else None

            total = _int_or_none(
                t.get("TotalPrizes") or t.get("totalPrizes") or t.get("total_prizes") or
                t.get("Total") or t.get("total") or t.get("PrintedPrizes") or t.get("printedPrizes")
            )
            remaining = _int_or_none(
                t.get("PrizesRemaining") or t.get("prizesRemaining") or t.get("prizes_remaining") or
                t.get("Remaining") or t.get("remaining") or
                t.get("UnclaimedPrizes") or t.get("unclaimedPrizes")
            )

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      odds,
                "prizes_total":     total,
                "prizes_remaining": remaining if remaining is not None else 0,
            })
        return tiers

    @staticmethod
    def _estimate_tickets(
        tiers: list[dict], overall_odds: float | None
    ) -> tuple[int | None, int | None]:
        total_printed   = sum(t["prizes_total"] or 0 for t in tiers if t.get("prizes_total"))
        total_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)

        if overall_odds and total_printed > 0:
            return round(overall_odds * total_printed), round(overall_odds * total_remaining)

        estimates = [
            round(t["prizes_total"] * t["odds_one_in"])
            for t in tiers
            if t.get("prizes_total") and t.get("odds_one_in")
        ]
        if estimates:
            estimates.sort()
            total_tickets = estimates[len(estimates) // 2]
            tickets_remaining = (
                round(total_tickets * total_remaining / total_printed)
                if total_printed > 0 else None
            )
            return total_tickets, tickets_remaining

        return None, None

    # ── HTML fallback (Vue-rendered content) ──────────────────────────────────

    def _parse_html(self, html: str) -> list[dict]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        games = []
        for card in soup.find_all(attrs={"class": re.compile(r"game|scratch|ticket", re.I)}):
            try:
                game = self._parse_html_card(card)
                if game:
                    games.append(game)
            except Exception:
                pass
        return games

    def _parse_html_card(self, card) -> dict | None:
        text = card.get_text(" ", strip=True)

        name_el = card.find(["h1", "h2", "h3", "h4"])
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            return None

        pm = re.search(r"\$(\d+(?:\.\d+)?)\s*(?:ticket)?", text, re.I)
        if not pm:
            return None
        price = float(pm.group(1))

        game_id_raw = ""
        link = card.find("a", href=True)
        if link:
            m = re.search(r"/scratch-offs?/(\d+)", link["href"])
            if m:
                game_id_raw = m.group(1)
        if not game_id_raw:
            m = re.search(r"#?(\d{3,})", text)
            if m:
                game_id_raw = m.group(1)
        if not game_id_raw:
            return None

        tiers = []
        for table in card.find_all("table"):
            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) >= 2:
                    prize = parse_prize_amount(cells[0])
                    if prize and prize > 0:
                        remaining = _int_or_none(cells[-1].replace(",", ""))
                        tiers.append({
                            "prize_amount":     prize,
                            "odds_one_in":      None,
                            "prizes_total":     None,
                            "prizes_remaining": remaining or 0,
                        })

        if not tiers:
            return None

        return self.build_game(
            game_id=f"oh{game_id_raw}",
            name=name,
            price=price,
            tiers=tiers,
            detail_url=f"{BASE_URL}/games/scratch-offs/{game_id_raw}",
        )


def _int_or_none(val) -> int | None:
    if val is None:
        return None
    try:
        return int(str(val).replace(",", "").split(".")[0])
    except (ValueError, TypeError):
        return None
