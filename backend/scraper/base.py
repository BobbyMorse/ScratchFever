from __future__ import annotations
import logging
import re
from abc import ABC, abstractmethod

import requests
from bs4 import BeautifulSoup

from backend.ev_calculator import (
    annuity_present_value,
    calculate_ev,
    calculate_jackpot_odds,
    effective_prize_value,
    find_top_prize,
    find_top_tier,
    parse_prize_amount,
    parse_odds,
)

logger = logging.getLogger(__name__)

# Name patterns → (periods_per_year, default_years).
# default_years=None means the year count is captured from the regex (group 2).
# Amount group accepts K/M/B suffixes ("$10K A WEEK FOR LIFE") via parse_prize_amount.
_ANNUITY_PATTERNS = [
    (re.compile(r"(\$?[\d,.]+\s*[KMB]?)\s*(?:a|per)?\s*week\s+for\s+life", re.I), 52, 20),
    (re.compile(r"(\$?[\d,.]+\s*[KMB]?)\s*(?:a|per)?\s*year\s+for\s+life", re.I), 1, 20),
    (re.compile(r"(\$?[\d,.]+\s*[KMB]?)\s*(?:a|per)?\s*month\s+for\s+life", re.I), 12, 20),
    (re.compile(r"(\$?[\d,.]+\s*[KMB]?)\s*(?:a|per)?\s*day\s+for\s+life", re.I), 365, 20),
    (re.compile(r"(\$?[\d,.]+\s*[KMB]?)\s*(?:a|per)?\s*week\s+for\s+(\d+)\s*year", re.I), 52, None),
    (re.compile(r"(\$?[\d,.]+\s*[KMB]?)\s*(?:a|per)?\s*year\s+for\s+(\d+)\s*year", re.I), 1, None),
]

# Per-tier "for-life" prize strings encountered in state APIs. These strings
# don't parse as a numeric amount, so without targeted handling the tier
# silently drops and the base annuity heuristic mis-applies NPV to the
# next-best (regular cash) tier — exploding EV. Separator between amount and
# period is permissive: "/", whitespace, or nothing (FL has "$50,000YR/LIFE").
# Examples handled:
#   NY: "$10K/Wk/Life", "$10,000/WEEK/LIFE"
#   FL: "$10,000/WK/LIFE", "$2,500 WK/LIFE", "$50,000YR/LIFE"
FOR_LIFE_TIER_RE = re.compile(
    r"(\$?[\d,.]+\s*[KMB]?)\s*[/\s]?\s*(WK|WEEK|MO|MONTH|YR|YEAR|DAY)\s*/\s*LIFE",
    re.I,
)
_FOR_LIFE_PERIODS_PER_YEAR = {
    "WK": 52, "WEEK": 52,
    "MO": 12, "MONTH": 12,
    "YR": 1, "YEAR": 1,
    "DAY": 365,
}
FOR_LIFE_DEFAULT_YEARS = 20


def parse_for_life_tier(raw: str) -> tuple[float, float, float] | None:
    """Parse a state-API per-tier for-life prize string. Returns
    (per_period_amount, annual, cash_value_NPV) or None."""
    if not raw:
        return None
    m = FOR_LIFE_TIER_RE.search(raw)
    if not m:
        return None
    per_period = parse_prize_amount(m.group(1))
    if not per_period or per_period <= 0:
        return None
    periods = _FOR_LIFE_PERIODS_PER_YEAR.get(m.group(2).upper())
    if not periods:
        return None
    annual = per_period * periods
    cash = annuity_present_value(annual, FOR_LIFE_DEFAULT_YEARS)
    if cash <= 0:
        return None
    return per_period, annual, cash


def _apply_annuity_heuristic(name: str, tiers: list[dict]) -> None:
    """For "FOR LIFE" games like "$10,000 A WEEK FOR LIFE", the prize-table top tier
    ($10,000) is the per-period payment, not the lump sum a winner receives. We assume
    winners always take the cash option, so populate the top tier with cash_value =
    PV of the 20-year guaranteed-minimum stream at 4% (matches state lotteries' published
    cash options within a few %). Face prize_amount is preserved for display continuity
    with the lottery's prize table; scraper-supplied cash_value is never overwritten.
    """
    if not tiers or not name:
        return
    # Scrapers that decode for-life tiers themselves (e.g. NY's "$10K/Wk/Life")
    # mark them with is_annuity. Don't double-apply.
    if any(t.get("is_annuity") for t in tiers):
        return
    top = max(tiers, key=lambda t: t.get("prize_amount", 0))
    if top.get("cash_value"):
        return
    for pattern, periods_per_year, default_years in _ANNUITY_PATTERNS:
        m = pattern.search(name)
        if not m:
            continue
        per_period = parse_prize_amount(m.group(1))
        if not per_period:
            continue
        years = default_years if default_years is not None else int(m.group(2))
        annual = per_period * periods_per_year
        cash = annuity_present_value(annual, years)
        if cash <= 0:
            return
        # Sanity guard: when a scraper silently drops the real for-life tier
        # (because the prize was a non-numeric string the parser couldn't read),
        # `top` is actually a regular cash tier with a much smaller face. Applying
        # the NPV to it explodes EV. MA-style for-life games have face within ~2x
        # of NPV; anything beyond 10x is the wrong tier.
        face = top.get("prize_amount") or 0
        if face > 0 and cash / face > 10:
            return
        top["is_annuity"] = True
        top["annuity_annual"] = annual
        top["annuity_years"] = years
        top["cash_value"] = round(cash, 2)
        return


# Game names that the annuity heuristic should match. Used by the sanity
# checker to flag for-life-named games that ended up with no annuity tier.
_FOR_LIFE_NAME_HINT_RE = re.compile(
    r"\b(?:for\s+life|a\s+(?:week|month|year|day)\s+for\s+life)\b",
    re.I,
)


def _check_sanity(state_code: str, name: str, price: float, tiers: list[dict],
                  ev_data: dict, tickets_remaining: int | None,
                  top_tier: dict) -> bool:
    """Post-build sanity checks. Each corresponds to a bug class we've seen
    ship to production. Returns True if EV numbers are trustworthy, False if
    the math is visibly broken — in which case build_game nulls ev/return_pct
    so the row can't rank or display a +EV badge.

    Threshold philosophy: set so that firing means "the math broke" (upstream
    parser produced impossible inputs), not "this game is hot." A late-stage
    game with legitimate +EV stays well below the rejection thresholds.
    """
    rp = ev_data.get("return_pct")
    ev = ev_data.get("ev")
    trustworthy = True

    # 1. Outlier return %. Above 300% is not a real scratch-off — it means a
    #    price or tier parse failed upstream (e.g. 2026-06-11 MN bug: the price
    #    regex caught $1 from a title like "Red Hot $1,000's" instead of the
    #    real $10 price, inflating return % by exactly 10×).
    if rp is not None and rp > 300:
        logger.error(
            "[%s] REJECT EV for %r: return_pct=%.1f%% is impossible "
            "(price=$%s, tickets_remaining=%s). Dropped tier or mis-parsed price.",
            state_code, name, rp, price, tickets_remaining,
        )
        trustworthy = False

    # 2. EV per ticket > ticket price by more than 5x. Same signal as (1) but
    #    independent of tickets_remaining; catches bugs where prize_pool is
    #    inflated regardless of denominator.
    if ev is not None and price and ev > 5 * price:
        logger.error(
            "[%s] REJECT EV for %r: ev=$%.2f > 5×price=$%.2f. "
            "Check tier prize_amounts.",
            state_code, name, ev, price,
        )
        trustworthy = False

    # 3. Game name implies for-life but no tier is annuity-marked. Means either
    #    (a) the for-life tier was silently dropped (NY/FL/GA-class bug), or
    #    (b) the heuristic ratio guard fired (which itself implies the for-life
    #    tier was dropped). Warn only — the rest of the math may still be fine.
    if name and _FOR_LIFE_NAME_HINT_RE.search(name):
        if not any(t.get("is_annuity") for t in tiers):
            logger.warning(
                "[%s] %r looks like a for-life game but no annuity tier was "
                "found. The for-life prize may be encoded in an unrecognized "
                "format — add a fixture and extend parse_for_life_tier.",
                state_code, name,
            )

    return trustworthy


def _sum_prize_pool(tiers: list[dict]) -> float | None:
    """Sum of effective_prize_value × prizes_remaining across tiers. Uses cash_value for
    annuity tiers so for-life games show real lump-sum exposure, not face × remaining."""
    if not tiers:
        return None
    total = 0.0
    saw_any = False
    for t in tiers:
        remaining = t.get("prizes_remaining")
        if remaining is None:
            continue
        saw_any = True
        total += effective_prize_value(t) * remaining
    return round(total, 2) if saw_any else None


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


class BaseScraper(ABC):
    state_code: str = ""
    state_name: str = ""
    base_url: str = ""
    disabled: bool = False

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.timeout = 30

    def get(self, url: str, **kwargs) -> requests.Response:
        # Allow callers to override the default timeout — pop before merging
        # so we don't trigger "got multiple values for keyword argument
        # 'timeout'" when both this method and the caller specify it.
        kwargs.setdefault("timeout", 30)
        resp = self.session.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    def soup(self, url: str, **kwargs) -> BeautifulSoup:
        resp = self.get(url, **kwargs)
        return BeautifulSoup(resp.text, "lxml")

    def build_game(self, game_id: str, name: str, price: float, tiers: list[dict],
                   tickets_remaining: int = None, total_tickets: int = None,
                   detail_url: str = None, overall_odds: float = None,
                   image_url: str = None, end_date: str = None,
                   ev_approximate: bool = False,
                   start_date: str = None,
                   has_second_chance: bool = False,
                   second_chance_url: str = None) -> dict:
        _apply_annuity_heuristic(name, tiers)
        ev_data = calculate_ev(price, tiers, tickets_remaining)
        top_prize, top_prize_remaining = find_top_prize(tiers)
        jackpot_odds = calculate_jackpot_odds(tiers, tickets_remaining)
        top_tier = find_top_tier(tiers) if tiers else {}
        prize_pool_left = _sum_prize_pool(tiers)
        # Gate, don't just warn: if the EV math is visibly broken (impossible
        # return % from a parser bug upstream), null the bad fields so the row
        # cannot rank or display +EV. Better to show "—" than a fake #1.
        if not _check_sanity(self.state_code, name, price, tiers, ev_data,
                             tickets_remaining, top_tier):
            ev_data = {"ev": None, "return_pct": None}
            prize_pool_left = None
        return {
            "game_id": str(game_id),
            "name": name,
            "price": price,
            "ev": ev_data["ev"],
            "return_pct": ev_data["return_pct"],
            "overall_odds_one_in": overall_odds,
            "top_prize": top_prize,
            "top_prize_remaining": top_prize_remaining,
            "jackpot_odds_one_in": jackpot_odds,
            "total_tickets": total_tickets,
            "tickets_remaining": tickets_remaining,
            "prize_pool_left": prize_pool_left,
            "detail_url": detail_url,
            "image_url": image_url,
            "end_date": end_date,
            "start_date": start_date,
            "ev_approximate": ev_approximate,
            "top_prize_is_annuity": bool(top_tier.get("is_annuity")),
            "top_prize_cash_value": top_tier.get("cash_value"),
            "top_prize_annuity_years": top_tier.get("annuity_years"),
            "top_prize_annuity_annual": top_tier.get("annuity_annual"),
            "has_second_chance": bool(has_second_chance),
            "second_chance_url": second_chance_url,
            "tiers": tiers,
        }

    @abstractmethod
    def scrape(self) -> list[dict]:
        """Return list of game dicts (output of build_game)."""

    def safe_scrape(self) -> tuple[list[dict], str | None]:
        try:
            games = self.scrape()
            logger.info("%s: scraped %d games", self.state_code, len(games))
            return games, None
        except Exception as exc:
            logger.error("%s: scrape failed: %s", self.state_code, exc, exc_info=True)
            return None, str(exc)

    # ── helpers ────────────────────────────────────────────────────────────────

    def parse_table_tiers(self, table) -> list[dict]:
        """Generic parser for HTML prize tables.
        Looks for columns: prize, odds, total, remaining.
        """
        tiers = []
        rows = table.find_all("tr")
        header = rows[0] if rows else None
        col_map = {}
        if header:
            cells = header.find_all(["th", "td"])
            for i, cell in enumerate(cells):
                text = cell.get_text(strip=True).lower()
                # Check remaining/total/odds BEFORE prize so "Prizes Remaining"
                # doesn't get mapped to the prize column. Use is-None guards so the
                # first matching column wins (e.g. "Prizes Paid" can't overwrite "Prize Amount").
                if col_map.get("remaining") is None and ("remaining" in text or "left" in text or "unclaimed" in text):
                    col_map["remaining"] = i
                elif col_map.get("total") is None and ("total" in text or "print" in text):
                    col_map["total"] = i
                elif col_map.get("odds") is None and any(k in text for k in ("odd", "chance", "1 in", "probability")):
                    col_map["odds"] = i
                elif col_map.get("prize") is None and any(k in text for k in ("prize", "amount", "award", "win")):
                    col_map["prize"] = i

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            try:
                prize_idx = col_map.get("prize", 0)
                odds_idx = col_map.get("odds")
                prize = parse_prize_amount(cells[prize_idx].get_text(strip=True))
                odds = (
                    parse_odds(cells[odds_idx].get_text(strip=True))
                    if odds_idx is not None and len(cells) > odds_idx
                    else None
                )

                remaining = None
                total_from_rem = None
                if "remaining" in col_map and len(cells) > col_map["remaining"]:
                    rem_txt = cells[col_map["remaining"]].get_text(strip=True).replace(",", "")
                    m = re.match(r"(\d+)\s+of\s+(\d+)", rem_txt, re.IGNORECASE)
                    if m:
                        remaining = int(m.group(1))
                        total_from_rem = int(m.group(2))
                    else:
                        try:
                            remaining = int(float(rem_txt))
                        except (ValueError, TypeError):
                            pass

                total = None
                if "total" in col_map and len(cells) > col_map["total"]:
                    total_txt = cells[col_map["total"]].get_text(strip=True).replace(",", "")
                    try:
                        total = int(float(total_txt))
                    except (ValueError, TypeError):
                        pass
                if total is None:
                    total = total_from_rem

                if prize is not None and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds,
                        "prizes_remaining": remaining,
                        "prizes_total": total,
                    })
            except (IndexError, AttributeError):
                continue

        return tiers
