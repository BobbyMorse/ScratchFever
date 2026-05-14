"""
New Hampshire Lottery scratch-off scraper.
Uses Playwright to load the in-store game collection SPA and intercept the
JSON API calls that populate game data.

Game collection: https://www.nhlottery.com/game-collection/in-store?filters=scratchGame
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

COLLECTION_URL = "https://www.nhlottery.com/game-collection/in-store?filters=scratchGame"
BASE_URL = "https://www.nhlottery.com"


class NewHampshireScraper(BaseScraper):
    state_code = "NH"
    state_name = "New Hampshire"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("NH: playwright not installed — run: python -m playwright install chromium")
            return []

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    def _playwright_scrape(self) -> list[dict]:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        captured_lists: list[list[dict]] = []
        game_id_to_img: dict[str, str] = {}

        def on_response(response):
            url = response.url
            low = url.lower()
            # Capture ticket images
            if "nhlottery.com" in low and any(
                low.endswith(e) or (e + "?") in low
                for e in (".jpg", ".jpeg", ".png", ".gif", ".webp")
            ):
                clean = url.split("?")[0]
                for gid in re.findall(r"\b(\d{3,})\b", clean):
                    game_id_to_img.setdefault(gid, clean)
                return
            # Capture JSON API responses
            ct = response.headers.get("content-type", "")
            if "json" not in ct or "nhlottery.com" not in low:
                return
            try:
                data = response.json()
                games = _detect_games(data)
                if games:
                    captured_lists.append(games)
                    logger.info("NH: captured %d games from %s", len(games), url)
            except Exception:
                pass

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
            page.on("response", on_response)

            try:
                page.goto(COLLECTION_URL, wait_until="networkidle", timeout=30_000)
            except Exception as e:
                logger.warning("NH: collection page error: %s", e)

            if not captured_lists:
                try:
                    page.wait_for_timeout(8_000)
                except Exception:
                    pass

            # Find game detail links from the JS-rendered page
            soup = BeautifulSoup(page.content(), "lxml")
            detail_links = _find_game_links(soup)
            logger.info("NH: %d game detail links, %d API game lists captured",
                        len(detail_links), len(captured_lists))

            # Scrape individual game pages for prize/odds data
            game_details: dict[str, dict] = {}
            for game_id, detail_url in detail_links[:120]:
                detail_json = [None]

                def on_detail_response(resp, _store=detail_json):
                    ct = resp.headers.get("content-type", "")
                    if "json" not in ct or "nhlottery.com" not in resp.url.lower():
                        return
                    try:
                        d = resp.json()
                        if _has_prize_data(d) and _store[0] is None:
                            _store[0] = d
                    except Exception:
                        pass

                try:
                    page.on("response", on_detail_response)
                    page.goto(detail_url, wait_until="networkidle", timeout=20_000)
                    page.remove_listener("response", on_detail_response)

                    raw_text = ""
                    try:
                        raw_text = page.inner_text("body")
                    except Exception:
                        pass

                    parsed = _parse_detail(raw_text, detail_json[0])
                    if parsed:
                        gid = game_id or parsed.pop("_game_id", None)
                        if gid:
                            game_details[str(gid)] = parsed
                except Exception as e:
                    logger.debug("NH: detail error for %s: %s", detail_url, e)
                    try:
                        page.remove_listener("response", on_detail_response)
                    except Exception:
                        pass

            browser.close()

        logger.info("NH: %d images, %d detail pages parsed", len(game_id_to_img), len(game_details))

        if not captured_lists and not game_details:
            logger.warning("NH: no game data captured — SPA API may have changed")
            return []

        games = []
        seen: set[str] = set()
        for raw_list in captured_lists:
            for raw in raw_list:
                game = self._parse_game(raw, game_id_to_img, game_details)
                if game and game["game_id"] not in seen:
                    seen.add(game["game_id"])
                    games.append(game)

        logger.info("NH: %d games scraped", len(games))
        return games

    def _parse_game(self, raw: dict, img_map: dict, detail_map: dict) -> dict | None:
        game_id = str(
            raw.get("gameId") or raw.get("game_id") or raw.get("id") or
            raw.get("gameNumber") or raw.get("number") or ""
        ).strip()
        if not game_id:
            return None

        name = (
            raw.get("gameName") or raw.get("name") or raw.get("title") or
            raw.get("game_name") or ""
        ).strip()
        if not name:
            return None

        price_raw = (
            raw.get("ticketPrice") or raw.get("price") or
            raw.get("ticket_price") or raw.get("cost") or 0
        )
        if isinstance(price_raw, str):
            price = parse_prize_amount(price_raw)
        else:
            price = float(price_raw) if price_raw else None
        if not price:
            return None

        detail_url = (
            raw.get("detailUrl") or raw.get("url") or raw.get("link") or
            raw.get("gameUrl") or raw.get("slug") or ""
        )
        if detail_url and not detail_url.startswith("http"):
            detail_url = BASE_URL.rstrip("/") + "/" + detail_url.lstrip("/")
        detail_url = detail_url or COLLECTION_URL

        image_url = (
            raw.get("imageUrl") or raw.get("image") or raw.get("thumbnailUrl") or
            raw.get("ticketImageUrl") or raw.get("img") or ""
        )
        if image_url and not image_url.startswith("http"):
            image_url = BASE_URL.rstrip("/") + "/" + image_url.lstrip("/")
        image_url = image_url or img_map.get(game_id) or None

        detail = detail_map.get(game_id, {})
        tiers = detail.get("tiers") or _extract_tiers(raw)
        overall_odds = detail.get("overall_odds") or _extract_overall_odds(raw)
        total_tickets = detail.get("total_tickets")
        tickets_remaining = detail.get("tickets_remaining")

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=detail_url,
            image_url=image_url,
        )


# ── helpers ───────────────────────────────────────────────────────────────────

def _detect_games(data) -> list[dict] | None:
    """Recursively find a list of game-like dicts in an unknown JSON structure."""
    if isinstance(data, list) and len(data) >= 1:
        sample = [x for x in data[:5] if isinstance(x, dict)]
        game_keys = {
            "gameId", "game_id", "gameName", "name", "title",
            "ticketPrice", "price", "gameNumber",
        }
        if sample and all(game_keys & g.keys() for g in sample):
            return data
    if isinstance(data, dict):
        for key in ("games", "items", "data", "results", "products", "content", "records", "gameList"):
            val = data.get(key)
            if val:
                found = _detect_games(val)
                if found is not None:
                    return found
    return None


def _has_prize_data(data) -> bool:
    if not isinstance(data, dict):
        return False
    return any(k in data for k in ("prizeTiers", "prizes", "tiers", "overallOdds", "overall_odds", "oddsAndPrizes"))


def _find_game_links(soup) -> list[tuple[str | None, str]]:
    links: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    skip = {"game-collection", "how-to-play", "winners", "find-retailer",
            "prizes", "keno", "draw", "ilottery", "#", "jackpot", "login"}
    game_path_hints = ("/game/", "/scratch", "/instant", "/in-store/", "/games/")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        low = href.lower()
        if any(t in low for t in skip):
            continue
        if not any(p in low for p in game_path_hints):
            continue
        full = href if href.startswith("http") else f"{BASE_URL}{href}"
        if full in seen:
            continue
        seen.add(full)
        m = re.search(r"/(\d{3,})(?:/|$)", href)
        game_id = m.group(1) if m else None
        links.append((game_id, full))
    return links


def _parse_detail(text: str, api_data: dict | None) -> dict | None:
    result: dict = {}
    if api_data:
        result["tiers"] = _extract_tiers(api_data)
        result["overall_odds"] = _extract_overall_odds(api_data)
        return result if (result["tiers"] or result["overall_odds"]) else None

    # Text fallback (same "$prize odds N of M" pattern as AZ)
    m = re.search(r"Overall\s+Odds[:\s]+1\s+in\s+([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        result["overall_odds"] = float(m.group(1).replace(",", ""))

    tiers = []
    for line in text.split("\n"):
        line = line.strip()
        match = re.match(
            r"\$([\d,]+(?:\.\d+)?(?:\s*(?:Million|Thousand))?)"
            r"\s+([\d,]+(?:\.\d+)?)"
            r"\s+(\d[\d,]*)\s+of\s+(\d[\d,]*)",
            line, re.IGNORECASE,
        )
        if match:
            prize = parse_prize_amount("$" + match.group(1))
            if prize:
                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": float(match.group(2).replace(",", "")),
                    "prizes_remaining": int(match.group(3).replace(",", "")),
                    "prizes_total": int(match.group(4).replace(",", "")),
                })
    result["tiers"] = tiers
    return result if (tiers or result.get("overall_odds")) else None


def _extract_tiers(data: dict) -> list[dict]:
    raw_tiers = (
        data.get("prizeTiers") or data.get("prizes") or
        data.get("tiers") or data.get("oddsAndPrizes") or []
    )
    tiers = []
    for t in raw_tiers:
        if not isinstance(t, dict):
            continue
        prize_raw = (
            t.get("prizeAmount") or t.get("prize_amount") or
            t.get("amount") or t.get("prize") or 0
        )
        prize = parse_prize_amount(str(prize_raw)) if isinstance(prize_raw, str) else float(prize_raw or 0)
        if not prize:
            continue
        total = t.get("prizesTotal") or t.get("total") or t.get("totalPrizes") or t.get("prizes_total")
        remaining = t.get("prizesRemaining") or t.get("remaining") or t.get("unclaimed") or t.get("prizes_remaining")
        odds = t.get("oddsOneIn") or t.get("odds") or t.get("odds_one_in") or t.get("oddsOnIn")
        tiers.append({
            "prize_amount": prize,
            "odds_one_in": float(odds) if odds else None,
            "prizes_total": int(total) if total is not None else None,
            "prizes_remaining": int(remaining) if remaining is not None else None,
        })
    return tiers


def _extract_overall_odds(data: dict) -> float | None:
    raw = (
        data.get("overallOdds") or data.get("overall_odds") or
        data.get("cashOdds") or data.get("odds")
    )
    if not raw:
        return None
    try:
        s = str(raw).replace(",", "")
        if "in" in s.lower():
            m = re.search(r"[\d.]+$", s)
            return float(m.group()) if m else None
        return float(s)
    except (ValueError, TypeError):
        return None
