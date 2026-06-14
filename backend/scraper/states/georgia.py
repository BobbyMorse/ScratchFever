"""
Georgia Lottery scratch-off scraper.
API: https://www.galottery.com/api/v1/instant-games/games?size=1000

Second-chance: GA's /games/second-chance.html is a hub for digital
interactive games (Frogger, Rolling Jackpots) hosted at secondchancega.com
— entry codes come from non-winning scratch tickets but GA Lottery does
not publish a per-scratch-game eligibility list. has_second_chance stays
FALSE for all GA scratch games until a per-game source is found.

  Fields: gameId, gameName, validationStatus, ticketPrice (cents),
          prizeTiers[]: prizeAmount (1/100 cent = divide by 10000 for dollars),
          winningTickets (total printed), paidTickets (claimed)

Per-tier paidTickets is published and reliable across all observed games
(claim rates cluster tightly within each game), so prizes_remaining is
computed directly from winningTickets - paidTickets for every tier.
"""
from __future__ import annotations
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from backend.scraper.base import BaseScraper, HEADERS
from backend.ev_calculator import annuity_present_value, parse_prize_amount

logger = logging.getLogger(__name__)

API_URL = "https://www.galottery.com/api/v1/instant-games/games?size=1000&page=0"
BASE_URL = "https://www.galottery.com"

_API_HEADERS = {
    **HEADERS,
    "Referer": "https://www.galottery.com/en-us/games/scratchers/active-games.html",
    "Accept": "application/json",
}
_DETAIL_CONCURRENCY = 10
_DETAIL_TIMEOUT = 15

# GA encodes top-tier prizes as prizeAmount=0 in the API (likely an int-overflow
# convention). For "for-life" games this strips out the headline prize entirely.
# We reconstruct the per-period payment from the game name. The name is also
# sometimes truncated at ~28 chars ("MONOPOLY $1,000 A WEEK FOR LI"), so we
# require "A {period}" but not "FOR LIFE".
_GA_PERIODIC_RE = re.compile(
    r"(\$?[\d,.]+\s*[KMB]?)\s+(?:A|PER)\s+(WEEK|WK|MONTH|MO|YEAR|YR|DAY)\b",
    re.I,
)
_GA_PERIODS_PER_YEAR = {
    "WK": 52, "WEEK": 52,
    "MO": 12, "MONTH": 12,
    "YR": 1, "YEAR": 1,
    "DAY": 365,
}
_GA_FOR_LIFE_DEFAULT_YEARS = 20
# Real for-life prizes are scarce (1-20 winners). Anything beyond this is
# almost certainly a non-annuity top prize that we just can't recover from the
# API (e.g. "MILLION DOLLAR GIVEAWAY!" zero tier with 19 prizes — still leave
# dropped rather than risk a false-positive annuity conversion).
_GA_FOR_LIFE_MAX_TOTAL = 50


def _ga_for_life_from_name(name: str) -> tuple[float, float, float] | None:
    """If `name` looks like '$X a {period} for life', return (per_period, annual, NPV)."""
    if not name:
        return None
    m = _GA_PERIODIC_RE.search(name)
    if not m:
        return None
    per_period = parse_prize_amount(m.group(1))
    if not per_period or per_period <= 0:
        return None
    periods = _GA_PERIODS_PER_YEAR.get(m.group(2).upper())
    if not periods:
        return None
    annual = per_period * periods
    cash = annuity_present_value(annual, _GA_FOR_LIFE_DEFAULT_YEARS)
    if cash <= 0:
        return None
    return per_period, annual, cash


def _fetch_overall_odds(game_id: str) -> float | None:
    url = f"{BASE_URL}/en-us/games/scratchers/{game_id}.html"
    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Referer": BASE_URL + "/en-us/games/scratchers/active-games.html"},
            timeout=_DETAIL_TIMEOUT,
        )
        resp.raise_for_status()
        m = re.search(r"1 in (\d+(?:\.\d+)?)", resp.text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    except Exception as exc:
        logger.warning("GA: odds fetch failed for game %s: %s", game_id, exc)
    return None


class GeorgiaScraper(BaseScraper):
    state_code = "GA"
    state_name = "Georgia"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(API_URL, headers=_API_HEADERS)
        data = resp.json()
        raw_games = data.get("games", []) if isinstance(data, dict) else data
        now_ms = time.time() * 1000
        active = [
            g for g in raw_games
            if g.get("validationStatus") == "ACTIVE"
            and (g.get("disableDate") or 0) > now_ms
        ]
        logger.info("GA: %d active games from API (of %d total)", len(active), len(raw_games))

        game_ids = [str(g.get("gameId", "")) for g in active if g.get("gameId")]
        odds_map: dict[str, float | None] = {}

        with ThreadPoolExecutor(max_workers=_DETAIL_CONCURRENCY) as executor:
            future_to_id = {executor.submit(_fetch_overall_odds, gid): gid for gid in game_ids}
            for future in as_completed(future_to_id):
                odds_map[future_to_id[future]] = future.result()

        fetched = sum(1 for v in odds_map.values() if v is not None)
        logger.info("GA: fetched overall odds for %d/%d games", fetched, len(game_ids))

        games = []
        for g in active:
            game = self._parse_game(g, odds_map)
            if game:
                games.append(game)

        logger.info("GA: %d games parsed", len(games))
        return games

    def _parse_game(self, g: dict, odds_map: dict) -> dict | None:
        name = (g.get("gameName") or "").strip().title()
        if not name:
            return None

        game_id = str(g.get("gameId", ""))
        price_cents = g.get("ticketPrice") or 0
        price = price_cents / 100.0
        if not price:
            return None

        image_url = (
            f"{BASE_URL}/content/dam/portal/images/scratchers-games/{game_id}/thumb.png"
            if game_id else None
        )

        tiers_raw = g.get("prizeTiers") or []
        tiers = []
        total_prizes_printed = 0
        total_prizes_remaining = 0
        for_life = _ga_for_life_from_name(name)

        for t in tiers_raw:
            prize_cents = t.get("prizeAmount") or 0
            prize = prize_cents / 10000.0
            total = int(t.get("winningTickets") or 0)
            paid = int(t.get("paidTickets") or 0)
            if total <= 0:
                continue

            annuity_annual = annuity_years = cash_value = None
            if prize <= 0:
                # GA encodes for-life and (sometimes) very-high cash top prizes as 0.
                # Reconstruct from the game name when it implies a for-life payout
                # and the count is small enough to plausibly be the annuity tier.
                if for_life and total <= _GA_FOR_LIFE_MAX_TOTAL:
                    per_period, annuity_annual, cash_value = for_life
                    prize = per_period
                    annuity_years = _GA_FOR_LIFE_DEFAULT_YEARS
                else:
                    continue

            remaining = max(0, total - paid)
            total_prizes_printed += total
            total_prizes_remaining += remaining
            tier = {
                "prize_amount":     prize,
                "odds_one_in":      None,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            }
            if cash_value is not None:
                tier["is_annuity"] = True
                tier["annuity_annual"] = annuity_annual
                tier["annuity_years"] = annuity_years
                tier["cash_value"] = round(cash_value, 2)
            tiers.append(tier)

        if not tiers:
            return None

        overall_odds = odds_map.get(game_id)
        total_tickets = None
        tickets_remaining = None

        if overall_odds and total_prizes_printed > 0:
            total_tickets = round(total_prizes_printed * overall_odds)
            depletion = (total_prizes_printed - total_prizes_remaining) / total_prizes_printed
            tickets_remaining = max(0, round(total_tickets * (1.0 - depletion)))

        end_date = None
        disable_ms = g.get("disableDate")
        if disable_ms:
            end_date = datetime.fromtimestamp(disable_ms / 1000, tz=timezone.utc).date().isoformat()

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            tickets_remaining=tickets_remaining,
            total_tickets=total_tickets,
            overall_odds=overall_odds,
            detail_url=f"{BASE_URL}/en-us/games/scratchers/{game_id}.html",
            image_url=image_url,
            end_date=end_date,
            ev_approximate=False,
        )
