"""
Illinois Lottery scratch-off scraper.

Cloudflare in front of illinoislottery.com TLS-fingerprints the urllib3 client
that `requests` uses and 403s it even with a perfect browser UA. `curl_cffi`
impersonates a real Chrome JA3/JA4 fingerprint and gets through, with no need
for Playwright.

Three server-rendered sources:

  1. /about-the-games/unpaid-instant-games-prizes
     Single table; one row per active game with these cells:
       [0] NAME ($PRICE)
       [1] $PRICE
       [2] GAME_NUMBER<br/>(WEEKS_IN_MARKET)
       [3] Prize Values        — br-separated dollar amounts
       [4] Total prizes        — br-separated counts, aligns with cell [3]
       [5] Unclaimed prizes    — br-separated counts, aligns with cell [3]

  2. /games-hub/instant-tickets?page=N&filter=all
     Paginated 20 cards per page (~58 total games). Used only to enumerate
     detail-page slugs from <a href="/games-hub/instant-tickets/{slug}">.

  3. /games-hub/instant-tickets/{slug}
     Per-game detail page with:
       - "Game Number | 7647" row in the spec table (authoritative join key)
       - "Overall Odds | 1 in 4.97"  OR  "Overall Odds | 4.80 to 1"
       - itg-details-block ticket image keyed by game number

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
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

BASE_URL = "https://www.illinoislottery.com"
PRIZES_URL = f"{BASE_URL}/about-the-games/unpaid-instant-games-prizes"
HUB_URL = f"{BASE_URL}/games-hub/instant-tickets"

# IL detail pages use two odds formats interchangeably:
#   "1 in 4.97"   (most games)
#   "4.80 to 1"   (some games)
_OVERALL_ODDS_IN_RE = re.compile(r"1\s*in\s*([\d,.]+)", re.IGNORECASE)
_OVERALL_ODDS_TO_RE = re.compile(r"([\d,.]+)\s*to\s*1", re.IGNORECASE)
_HUB_DETAIL_HREF = "/games-hub/instant-tickets/"
_IMPERSONATE = "chrome120"


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


def _parse_odds_value(text: str) -> float | None:
    """Parse IL's 'Overall Odds' cell into the 1-in-N divisor."""
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


class IllinoisScraper(BaseScraper):
    state_code = "IL"
    state_name = "Illinois"
    base_url = BASE_URL
    # ~58 detail-page fetches (parallel, 8 workers) + 3 hub pages + 1 prizes
    # page. Wall-clock typically ~15s; 600s leaves headroom for retries.
    scraper_timeout = 600

    # ── Cloudflare-impersonating fetcher ───────────────────────────────────────
    def _cf_get(self, url: str) -> str:
        resp = cffi_requests.get(url, impersonate=_IMPERSONATE, timeout=30)
        resp.raise_for_status()
        return resp.text

    def _cf_soup(self, url: str) -> BeautifulSoup:
        return BeautifulSoup(self._cf_get(url), "lxml")

    # ── main ────────────────────────────────────────────────────────────────────
    def scrape(self) -> list[dict]:
        slugs = self._fetch_hub_slugs()
        logger.info("IL: discovered %d hub slugs", len(slugs))

        detail_map = self._fetch_detail_map(slugs)
        logger.info("IL: resolved %d detail-page records", len(detail_map))

        soup = self._cf_soup(PRIZES_URL)
        rows = soup.select("tr.unclaimed-prizes-table__row")
        logger.info("IL: %d unclaimed-prize rows", len(rows))

        games: list[dict] = []
        for row in rows:
            g = self._build_game_from_row(row, detail_map)
            if g:
                games.append(g)

        with_ev = sum(1 for g in games if g.get("ev") is not None)
        logger.info("IL: %d games scraped, %d with EV", len(games), with_ev)
        return games

    # ── hub: enumerate slugs across all paginated pages ────────────────────────
    def _fetch_hub_slugs(self) -> set[str]:
        slugs: set[str] = set()
        # Hub has ~3 pages (20+20+18 ≈ 58). 5 cap defends against pagination changes.
        for page in range(0, 5):
            url = f"{HUB_URL}?page={page}&filter=all"
            try:
                soup = self._cf_soup(url)
            except Exception as e:
                logger.warning("IL: hub page %d fetch failed: %s", page, e)
                break

            before = len(slugs)
            for link in soup.select(f'a[href*="{_HUB_DETAIL_HREF}"]'):
                href = link.get("href") or ""
                if _HUB_DETAIL_HREF not in href:
                    continue
                slug = href.split(_HUB_DETAIL_HREF, 1)[1].split("?")[0].strip("/")
                if not slug or "/" in slug:
                    continue
                slugs.add(slug)

            added = len(slugs) - before
            logger.debug("IL: hub page %d → +%d slugs (total %d)", page, added, len(slugs))
            if added == 0 and page > 0:
                break
        return slugs

    # ── per-slug detail-page fetch (parallel) ──────────────────────────────────
    def _fetch_detail_map(self, slugs: set[str]) -> dict[str, dict]:
        """Returns {game_number: {slug, overall_odds, image_url, detail_url}}."""

        def _fetch(slug: str) -> tuple[str | None, dict | None]:
            url = f"{BASE_URL}{_HUB_DETAIL_HREF}{slug}"
            try:
                html = self._cf_get(url)
            except Exception as e:
                logger.debug("IL: detail fetch failed for %s: %s", slug, e)
                return None, None
            soup = BeautifulSoup(html, "lxml")

            game_number = None
            overall_odds = None
            for tr in soup.select("table.itg-details-block--table tr"):
                cells = tr.find_all("td")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(" ", strip=True).lower()
                value = cells[1].get_text(" ", strip=True)
                if "game number" in label:
                    m = re.search(r"(\d{3,6})", value)
                    if m:
                        game_number = m.group(1)
                elif "overall odds" in label:
                    overall_odds = _parse_odds_value(value)

            image_url = None
            img = soup.select_one(".itg-details-block .cmp-image img[src]")
            if img:
                src = img.get("src") or ""
                if src.startswith("/"):
                    src = BASE_URL + src
                image_url = src

            if not game_number:
                return None, None
            return game_number, {
                "slug": slug,
                "overall_odds": overall_odds,
                "image_url": image_url,
                "detail_url": url,
            }

        detail_map: dict[str, dict] = {}
        # 8 workers: keeps IL CDN happy, finishes ~58 fetches in ~5-10s.
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(_fetch, slug) for slug in slugs]
            for fut in as_completed(futs):
                gn, rec = fut.result()
                if gn and rec and gn not in detail_map:
                    detail_map[gn] = rec
        return detail_map

    # ── prizes-table row → game dict ───────────────────────────────────────────
    def _build_game_from_row(self, row, detail_map: dict[str, dict]) -> dict | None:
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

        detail = detail_map.get(game_number) or {}
        overall_odds = detail.get("overall_odds")
        detail_url = detail.get("detail_url") or f"{BASE_URL}/games-hub/instant-tickets"
        image_url = detail.get("image_url")

        tickets_remaining = None
        total_tickets = None
        if tiers and overall_odds and overall_odds > 0:
            total_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
            total_printed = sum(t.get("prizes_total") or 0 for t in tiers)
            if total_remaining > 0:
                tickets_remaining = round(overall_odds * total_remaining)
            if total_printed > 0:
                total_tickets = round(overall_odds * total_printed)
            if total_tickets:
                for t in tiers:
                    if t.get("prizes_total"):
                        t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            tickets_remaining=tickets_remaining,
            total_tickets=total_tickets,
            overall_odds=overall_odds,
            detail_url=detail_url,
            image_url=image_url,
        )
