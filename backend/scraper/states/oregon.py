"""
Oregon Lottery scratch-off scraper.

Implementation note (2026-05-31): switched from per-game Playwright DOM
scraping to OR's JSON API at osl-gameinfo-sys-api.us-w2.cloudhub.io.
- The site's JS bundle authenticates via client_id/client_secret headers.
  We bootstrap by loading the listing page once with Playwright and
  intercepting those headers from the first XHR, then make all subsequent
  per-game calls with `requests`.
- This avoids 36 sequential JS-rendered detail pageloads (and the timing
  fragility that caused prod scrapes to return 1-row partial results).
"""
from __future__ import annotations
import re
import json
import logging
import concurrent.futures
from datetime import datetime, timezone

import requests
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://www.oregonlottery.org"
LISTING_URL = f"{BASE_URL}/scratch-its/list/"
API_BASE = "https://osl-gameinfo-sys-api.us-w2.cloudhub.io/api/v1/instant/games"


class OregonScraper(BaseScraper):
    state_code = "OR"
    state_name = "Oregon"
    base_url = BASE_URL
    scraper_timeout = 300

    def scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("OR: playwright not installed")
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._scrape_via_api).result()

    def _scrape_via_api(self) -> list[dict]:
        creds, listing, image_map = self._bootstrap_via_playwright()
        if not creds:
            raise RuntimeError("OR: failed to capture API credentials from listing page")
        if not listing:
            raise RuntimeError("OR: listing API call returned no data")

        all_games = listing.get("InstantGames", [])
        logger.info(
            "OR: API returned %d total games, %d images sniffed from listing",
            len(all_games), len(image_map),
        )

        active = [g for g in all_games if _is_active(g)]
        logger.info("OR: %d active games after filtering", len(active))

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": f"{BASE_URL}/",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        })

        games: list[dict] = []
        for meta in active:
            try:
                game = self._fetch_and_build(session, meta, image_map)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("OR: detail fetch failed for game %s: %s",
                             meta.get("GameNumber"), e)

        logger.info("OR: %d games scraped", len(games))
        return games

    def _bootstrap_via_playwright(self) -> tuple[dict | None, dict | None, dict[str, str]]:
        from playwright.sync_api import sync_playwright

        creds: dict[str, str] = {}
        listing_payload: dict | None = None
        # Sniff every image response on the listing page and key by leading
        # game-number prefix in the filename (e.g. "1652_50Or100-Blast_*.jpg").
        image_map: dict[str, str] = {}

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ))
            page = ctx.new_page()

            def on_request(req):
                if "osl-gameinfo-sys-api" in req.url and "client_id" in req.headers and not creds:
                    creds["client_id"] = req.headers["client_id"]
                    creds["client_secret"] = req.headers["client_secret"]

            def on_response(resp):
                nonlocal listing_payload
                if (
                    "osl-gameinfo-sys-api" in resp.url
                    and "count=1000" in resp.url
                    and listing_payload is None
                    and resp.status == 200
                ):
                    try:
                        listing_payload = json.loads(resp.body())
                    except Exception:
                        pass

                # Filenames in /wp-content/uploads/.../{GameNum}_*.jpg key to
                # the game; prefer the larger non-thumbnail variant.
                url = resp.url
                low = url.lower()
                if "/wp-content/uploads/" in low and any(
                    low.split("?")[0].endswith(ext)
                    for ext in (".jpg", ".jpeg", ".png", ".webp")
                ):
                    fname = url.rsplit("/", 1)[-1].split("?")[0]
                    m = re.match(r"(\d+)_", fname)
                    if m:
                        gid = m.group(1)
                        existing = image_map.get(gid, "")
                        # Skip resized thumbnails (-WxH suffix) when a base
                        # version is already captured.
                        is_thumb = bool(re.search(r"-\d+x\d+\.[a-z]+$", fname, re.I))
                        if not existing or (
                            not is_thumb and re.search(r"-\d+x\d+\.[a-z]+$", existing, re.I)
                        ):
                            image_map[gid] = url.split("?")[0]

            page.on("request", on_request)
            page.on("response", on_response)

            # Race the navigation against the API response so we don't return
            # early if networkidle fires before the JSON endpoint finishes.
            try:
                with page.expect_response(
                    lambda r: "osl-gameinfo-sys-api" in r.url and "count=1000" in r.url,
                    timeout=45_000,
                ):
                    page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception as e:
                logger.warning("OR: listing page load: %s", e)

            browser.close()

        return (creds if creds else None), listing_payload

    def _fetch_and_build(self, session: requests.Session, meta: dict) -> dict | None:
        game_num = str(meta.get("GameNumber") or "").strip()
        if not game_num:
            return None
        name = (meta.get("GameNameTitle") or "").strip()
        price = _to_float(meta.get("TicketPrice"))
        if not name or not price:
            return None

        resp = session.get(API_BASE, params={
            "gameNumber": game_num,
            "includePrizeTiers": "true",
        }, timeout=15)
        if resp.status_code != 200:
            logger.debug("OR: game %s api %s", game_num, resp.status_code)
            return None

        data = resp.json()
        detail = _first_dict(data, "InstantGames") or data
        if not isinstance(detail, dict):
            return None

        overall_odds = _to_float(detail.get("OverallOdds") or meta.get("OverallOdds"))

        tiers = []
        for t in (detail.get("PrizeTiers") or []):
            prize = _to_float(t.get("PrizeAmount"))
            total = _to_int(t.get("PrizesTotal"))
            remaining = _to_int(t.get("PrizesRemaining"))
            odds = _to_float(t.get("Odds"))
            if not prize or prize <= 0 or not total:
                continue
            tiers.append({
                "prize_amount": prize,
                "prizes_total": total,
                "prizes_remaining": remaining,
                # API "Odds" is probability per ticket; convert to "1 in N".
                "odds_one_in": (round(1 / odds, 2) if odds and odds > 0 else None),
            })

        if not tiers:
            return None

        total_printed = sum(t["prizes_total"] or 0 for t in tiers)
        total_remaining = sum(t["prizes_remaining"] or 0 for t in tiers)

        total_tickets = None
        tickets_remaining = None
        if overall_odds and total_printed > 0:
            total_tickets = round(overall_odds * total_printed)
            tickets_remaining = round(overall_odds * total_remaining)

        return self.build_game(
            game_id=game_num,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=f"{BASE_URL}/scratch-its/{_slugify(name)}/",
        )


def _is_active(g: dict) -> bool:
    """A game is for-sale if GameEndDate (sales end) is in the future or unset.
    ValidationEndDate is later — it's the claim deadline — and including those
    games would inflate the active list with dead-inventory titles."""
    end = g.get("GameEndDate")
    if not end:
        return True
    try:
        dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc)
    except Exception:
        return True


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _first_dict(payload, list_key: str):
    """Some endpoints wrap a single game in {InstantGames: [game]}, others return game dict."""
    if isinstance(payload, dict):
        if list_key in payload and isinstance(payload[list_key], list) and payload[list_key]:
            return payload[list_key][0]
    return None


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
