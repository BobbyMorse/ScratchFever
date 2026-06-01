"""
Illinois Lottery scratch-off scraper.

Three server-rendered sources (no JS / Playwright needed; the page accepts a
standard browser User-Agent and returns full HTML):

  1. https://www.illinoislottery.com/about-the-games/unpaid-instant-games-prizes
     Single table with one row per active game. Cells:
       [0] NAME ($PRICE)
       [1] $PRICE
       [2] GAME_NUMBER<br/>(WEEKS_IN_MARKET)
       [3] Prize Values        — br-separated dollar amounts
       [4] Total prizes        — br-separated counts, aligns with cell [3]
       [5] Unclaimed prizes    — br-separated counts, aligns with cell [3]

  2. https://www.illinoislottery.com/games-hub/instant-tickets?page=N&filter=all
     Paginated 20 cards per page (≈3 pages for ~58 games). Each card carries
     the slug in <a href="/games-hub/instant-tickets/{slug}"> and the IL game
     number inside the background-image URL as `IL-{number}_Logo*.png`.

  3. https://www.illinoislottery.com/games-hub/instant-tickets/{slug}
     Per-game detail page with an `Overall Odds | 1 in X.XX` row.

EV uses the MA formula:
  tickets_remaining = overall_odds × Σ prizes_remaining_i
  EV = Σ(prize_i × prizes_remaining_i) / tickets_remaining − price
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

BASE_URL = "https://www.illinoislottery.com"
PRIZES_URL = f"{BASE_URL}/about-the-games/unpaid-instant-games-prizes"
HUB_URL = f"{BASE_URL}/games-hub/instant-tickets"

_IL_IMG_GAMEID_RE = re.compile(r"IL-(\d{3,6})_", re.IGNORECASE)
# IL detail pages use two formats interchangeably for the Overall Odds cell:
#   "1 in 4.97"   (most games)
#   "4.80 to 1"   (some games, including 7-11-21 editions and Loose Change Multiplier)
_OVERALL_ODDS_IN_RE = re.compile(r"1\s*in\s*([\d,.]+)", re.IGNORECASE)
_OVERALL_ODDS_TO_RE = re.compile(r"([\d,.]+)\s*to\s*1", re.IGNORECASE)
_BG_IMG_URL_RE = re.compile(r"url\(([^)]+)\)")
_HUB_DETAIL_HREF = "/games-hub/instant-tickets/"


def _parse_int(text: str) -> int | None:
    cleaned = (text or "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return None


def _br_split(cell) -> list[str]:
    """Return a cell's text content split on <br> boundaries, trimmed."""
    raw = cell.decode_contents()
    parts = re.split(r"<br\s*/?>", raw, flags=re.I)
    out = []
    for p in parts:
        txt = re.sub(r"<[^>]+>", "", p).strip()
        if txt:
            out.append(txt)
    return out


class IllinoisScraper(BaseScraper):
    state_code = "IL"
    state_name = "Illinois"
    base_url = BASE_URL
    # ~58 games × ~0.5s detail fetch (parallel) + 3 hub pages + 1 prizes page.
    # 600s is comfortably above worst-case retry latency.
    scraper_timeout = 600

    # Cloudflare in front of illinoislottery.com TLS-fingerprints the urllib3
    # client `requests` uses and 403s it even with a perfect browser UA. curl_cffi
    # impersonates a real Chrome TLS handshake (JA3/JA4) which gets through.
    _IMPERSONATE = "chrome120"

    def _cf_soup(self, url: str) -> BeautifulSoup:
        resp = cffi_requests.get(url, impersonate=self._IMPERSONATE, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def scrape(self) -> list[dict]:
        hub_map = self._fetch_hub_map()
        logger.info("IL: %d games discovered in games-hub", len(hub_map))

        soup = self._cf_soup(PRIZES_URL)
        rows = soup.select("tr.unclaimed-prizes-table__row")
        logger.info("IL: %d unclaimed-prize rows", len(rows))

        parsed: list[dict] = []
        for row in rows:
            game = self._parse_row(row, hub_map)
            if game:
                parsed.append(game)

        # Fetch overall odds in parallel from each detail page.
        odds_map = self._fetch_odds_parallel(parsed)

        games: list[dict] = []
        for g in parsed:
            overall = odds_map.get(g["game_id"])
            tickets_remaining = None
            total_tickets = None
            tiers = g["tiers"]
            if tiers and overall and overall > 0:
                total_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
                total_printed = sum(t.get("prizes_total") or 0 for t in tiers)
                if total_remaining > 0:
                    tickets_remaining = round(overall * total_remaining)
                if total_printed > 0:
                    total_tickets = round(overall * total_printed)
                if total_tickets:
                    for t in tiers:
                        if t.get("prizes_total"):
                            t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

            games.append(self.build_game(
                game_id=g["game_id"],
                name=g["name"],
                price=g["price"],
                tiers=tiers,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                overall_odds=overall,
                detail_url=g["detail_url"],
                image_url=g["image_url"],
            ))

        with_ev = sum(1 for g in games if g.get("ev") is not None)
        logger.info("IL: %d games scraped, %d with EV", len(games), with_ev)
        return games

    # ── hub: slug + image map keyed by IL game number ──────────────────────────
    def _fetch_hub_map(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        # 3 pages × 20 cards covers ~58 active games; stop early if a page returns
        # nothing new (defends against IL adding/removing pagination params).
        for page in range(0, 5):
            url = f"{HUB_URL}?page={page}&filter=all"
            try:
                soup = self._cf_soup(url)
            except Exception as e:
                logger.warning("IL: hub page %d fetch failed: %s", page, e)
                break

            added = 0
            for card in soup.select("div.simple-game-card"):
                link = card.find("a", href=True)
                if not link:
                    continue
                href = link["href"]
                if _HUB_DETAIL_HREF not in href:
                    continue
                slug = href.rsplit("/", 1)[-1].split("?")[0]
                if not slug or slug == "instant-tickets":
                    continue
                name = (link.get("aria-label") or "").strip()

                banner = card.select_one(".simple-game-card__banner")
                image_url = None
                game_number = None
                if banner and banner.has_attr("style"):
                    m = _BG_IMG_URL_RE.search(banner["style"])
                    if m:
                        img = m.group(1).strip().strip("'\"")
                        if img.startswith("/"):
                            img = BASE_URL + img
                        image_url = img
                        gm = _IL_IMG_GAMEID_RE.search(img)
                        if gm:
                            game_number = gm.group(1)

                if not game_number:
                    continue
                if game_number in out:
                    continue
                out[game_number] = {
                    "slug": slug,
                    "image_url": image_url,
                    "hub_name": name,
                }
                added += 1

            logger.debug("IL: hub page %d → +%d games (total %d)", page, added, len(out))
            if added == 0 and page > 0:
                break
        return out

    # ── row → parsed game (without overall odds yet) ───────────────────────────
    def _parse_row(self, row, hub_map: dict[str, dict]) -> dict | None:
        cells = row.find_all("td")
        if len(cells) < 6:
            return None

        raw_name = cells[0].get_text(" ", strip=True)
        name = re.sub(r"\s*\(\s*\$[\d.,]+\s*\)\s*$", "", raw_name).strip()
        if not name:
            return None

        price = None
        price_attr = row.get("data-price")
        if price_attr:
            try:
                price = float(price_attr)
            except (TypeError, ValueError):
                pass
        if price is None:
            m = re.search(r"\(\s*\$([\d.,]+)\s*\)", raw_name)
            if m:
                try:
                    price = float(m.group(1).replace(",", ""))
                except ValueError:
                    pass
        if not price:
            return None

        # cells[2] looks like "7647\n(14)" — first number is the game number.
        gn_match = re.search(r"(\d{3,6})", cells[2].get_text(" ", strip=True))
        if not gn_match:
            return None
        game_number = gn_match.group(1)
        game_id = f"il{game_number}"

        prize_parts = _br_split(cells[3])
        total_parts = _br_split(cells[4])
        unclaimed_parts = _br_split(cells[5])

        tiers: list[dict] = []
        for i, prize_text in enumerate(prize_parts):
            amt = parse_prize_amount(prize_text)
            if amt is None or amt <= 0:
                continue
            total = _parse_int(total_parts[i]) if i < len(total_parts) else None
            remaining = _parse_int(unclaimed_parts[i]) if i < len(unclaimed_parts) else None
            if remaining is None and total is None:
                continue
            tiers.append({
                "prize_amount": amt,
                "prizes_remaining": remaining,
                "prizes_total": total,
                "odds_one_in": None,
            })

        hub = hub_map.get(game_number) or {}
        slug = hub.get("slug")
        detail_url = f"{BASE_URL}{_HUB_DETAIL_HREF}{slug}" if slug else f"{BASE_URL}/games-hub/instant-tickets"

        return {
            "game_id": game_id,
            "game_number": game_number,
            "slug": slug,
            "name": name,
            "price": price,
            "tiers": tiers,
            "detail_url": detail_url,
            "image_url": hub.get("image_url"),
        }

    # ── overall odds: scrape each detail page in parallel ──────────────────────
    def _fetch_odds_parallel(self, parsed: list[dict]) -> dict[str, float]:
        odds: dict[str, float] = {}
        targets = [g for g in parsed if g.get("slug")]

        def _fetch(g):
            url = g["detail_url"]
            try:
                resp = cffi_requests.get(url, impersonate=self._IMPERSONATE, timeout=30)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
            except Exception as e:
                logger.debug("IL: detail fetch failed for %s: %s", g["slug"], e)
                return g["game_id"], None
            return g["game_id"], _extract_overall_odds(soup)

        # 8 workers keeps IL's CDN happy while finishing ~58 fetches in ~5–8s.
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(_fetch, g) for g in targets]
            for fut in as_completed(futs):
                gid, val = fut.result()
                if val:
                    odds[gid] = val
        logger.info("IL: overall odds resolved for %d/%d games", len(odds), len(targets))
        return odds


def _parse_odds_value(text: str) -> float | None:
    """Parse IL's 'Overall Odds' cell, which appears as either '1 in X.XX'
    or 'X.XX to 1'. Both forms yield the same divisor."""
    for pattern in (_OVERALL_ODDS_IN_RE, _OVERALL_ODDS_TO_RE):
        m = pattern.search(text)
        if m:
            try:
                v = float(m.group(1).replace(",", ""))
                if v > 0:
                    return v
            except ValueError:
                pass
    return None


def _extract_overall_odds(soup: BeautifulSoup) -> float | None:
    """Find the 'Overall Odds | X' row in the detail-page spec table."""
    for tr in soup.select("table.itg-details-block--table tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True).lower()
        if "overall odds" in label:
            v = _parse_odds_value(cells[1].get_text(" ", strip=True))
            if v:
                return v
    # Fallback: any odds-format substring under the spec block.
    block = soup.select_one(".itg-details-block")
    if block:
        return _parse_odds_value(block.get_text(" ", strip=True))
    return None
