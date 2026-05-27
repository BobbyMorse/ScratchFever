"""
Indiana Hoosier Lottery scratch-off scraper.

Listing: https://hoosierlottery.com/games/scratch-off/
  <a data-id data-name data-price href="/games/scratch-off/{slug}/">

Detail: https://hoosierlottery.com/games/scratch-off/{slug}/
  table: Prize Amount | Total Winning Tickets | Unclaimed | Status
  [class*=odds]: "Estimated Overall Odds: 1 in X.XX"

Indiana publishes per-tier unclaimed counts but omits prizes below ~$40 and does
not publish total tickets printed:
  total_tickets is derived by modeling unpublished small prizes as avg_small per
  winning ticket and requiring: small_pool + pub_pool = total_tickets × price × payout.
  tickets_remaining = total_tickets × (published_remaining / published_total).
"""
from __future__ import annotations
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from backend.scraper.base import BaseScraper, HEADERS

logger = logging.getLogger(__name__)

GAMES_URL = "https://hoosierlottery.com/games/scratch-off/"
BASE_URL = "https://hoosierlottery.com"

_PAYOUT_RATE = 0.65  # Indiana Hoosier Lottery statewide prize payout rate
# Assumed average prize for unpublished small-prize tiers (prizes < $40 not shown)
_AVG_SMALL = {1: 2.0, 2: 3.0, 3: 4.0, 5: 6.0, 10: 10.0, 20: 15.0, 25: 18.0, 30: 20.0, 50: 25.0}
_DETAIL_CONCURRENCY = 10
_DETAIL_TIMEOUT = 15


def _estimate_total_tickets(
    price: float, overall_odds: float, pub_count: int, pub_pool: float
) -> int | None:
    """
    Infer total tickets printed when Indiana only publishes prizes >= $40.

    Models missing small prizes as avg_small per winning ticket:
      all_prizes        = total_tickets / overall_odds
      small_prizes      = all_prizes - pub_count
      small_prize_pool  = small_prizes × avg_small
      small_pool + pub_pool = total_tickets × price × payout_rate

    Solving for total_tickets:
      total = (pub_count × avg - pub_pool) / (avg / overall_odds - price × payout)
    """
    avg = _AVG_SMALL.get(round(price), 10.0)
    denom = avg / overall_odds - price * _PAYOUT_RATE
    if denom >= 0:
        return None
    num = pub_count * avg - pub_pool
    if num >= 0:
        return None
    total = int(num / denom)
    # Sanity: total must be larger than the minimum implied by odds alone
    return total if total > round(pub_count * overall_odds) else None


class IndianaScraper(BaseScraper):
    state_code = "IN"
    state_name = "Indiana"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        stubs: list[dict] = []
        seen: set[str] = set()

        for a in soup.find_all("a", attrs={"data-id": True}):
            href = a.get("href", "")
            if "/games/scratch-off/" not in href:
                continue
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug in seen:
                continue
            seen.add(slug)

            name = a.get("data-name", "").strip().title()
            if not name:
                continue
            try:
                price = float(a.get("data-price") or 0)
            except (ValueError, TypeError):
                price = None
            if not price:
                continue

            img = a.find("img")
            image_url = img["src"] if img and img.get("src") else None
            detail_url = (BASE_URL + href) if href.startswith("/") else href

            stubs.append({
                "game_id": str(a.get("data-id") or slug),
                "name": name,
                "price": price,
                "detail_url": detail_url,
                "image_url": image_url,
            })

        logger.info("IN: found %d games from listing", len(stubs))

        with ThreadPoolExecutor(max_workers=_DETAIL_CONCURRENCY) as executor:
            future_to_id = {
                executor.submit(self._fetch_detail, s["detail_url"]): s["game_id"]
                for s in stubs
            }
            detail_map = {future_to_id[f]: f.result() for f in as_completed(future_to_id)}

        games = []
        for stub in stubs:
            game = self._build(stub, detail_map.get(stub["game_id"], {}))
            if game:
                games.append(game)

        with_ev = sum(1 for g in games if g.get("ev") is not None)
        logger.info("IN: %d/%d games have estimated EV", with_ev, len(games))
        return games

    def _fetch_detail(self, url: str) -> dict:
        """Thread-safe detail page fetch (uses requests directly, not self.session)."""
        result: dict = {"overall_odds": None, "tiers": []}
        try:
            resp = requests.get(url, headers=HEADERS, timeout=_DETAIL_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            for el in soup.select("[class*=odds]"):
                m = re.search(
                    r"overall\s+odds[:\s]+1\s+in\s+([\d.]+)",
                    el.get_text(strip=True),
                    re.I,
                )
                if m:
                    result["overall_odds"] = float(m.group(1))
                    break

            for table in soup.find_all("table"):
                tiers = self.parse_table_tiers(table)
                if tiers:
                    result["tiers"] = tiers
                    break
        except Exception as exc:
            logger.warning("IN: detail fetch failed %s: %s", url, exc)
        return result

    def _build(self, stub: dict, detail: dict) -> dict:
        tiers = detail.get("tiers") or []
        overall_odds = detail.get("overall_odds")
        price = stub["price"]

        total_tickets = None
        tickets_remaining = None

        if tiers and overall_odds:
            pub_count = sum(t.get("prizes_total") or 0 for t in tiers)
            pub_pool = sum(
                (t.get("prize_amount") or 0) * (t.get("prizes_total") or 0)
                for t in tiers
            )
            pub_remaining = sum(
                t.get("prizes_remaining") or 0
                for t in tiers
                if t.get("prizes_remaining") is not None
            )

            if pub_count > 0 and pub_pool > 0:
                total_tickets = _estimate_total_tickets(price, overall_odds, pub_count, pub_pool)
                if total_tickets:
                    depletion = pub_remaining / pub_count
                    tickets_remaining = max(0, round(total_tickets * depletion))

                    # Indiana only publishes prizes >= ~$40; add a synthetic tier for
                    # the unpublished small-prize pool so calculate_ev captures the
                    # full ~65% payout rather than just the published portion.
                    all_winners = total_tickets / overall_odds
                    small_count = max(0.0, all_winners - pub_count)
                    small_pool = total_tickets * price * _PAYOUT_RATE - pub_pool
                    if small_count > 0 and small_pool > 0:
                        small_remaining = max(0, round(small_count * depletion))
                        tiers = tiers + [{
                            "prize_amount": round(small_pool / small_count, 2),
                            "odds_one_in": None,
                            "prizes_remaining": small_remaining,
                            "prizes_total": round(small_count),
                        }]

        return self.build_game(
            game_id=stub["game_id"],
            name=stub["name"],
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            tickets_remaining=tickets_remaining,
            total_tickets=total_tickets,
            detail_url=stub["detail_url"],
            image_url=stub["image_url"],
            ev_approximate=True,
        )
