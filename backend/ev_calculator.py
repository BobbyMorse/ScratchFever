"""
Expected Value calculator for scratch-off lottery tickets.

EV = sum(prize_i * prob_i) - ticket_price

When remaining prize data is available:
  prob_i = prizes_remaining_i / total_tickets_remaining

When only initial odds are available:
  prob_i = 1 / odds_one_in_i

Return % = (EV + price) / price * 100
  >100% → positive expected value (rare, happens when prizes cluster toward end of run)
  ~60-70% → typical scratch ticket
"""
from __future__ import annotations


ANNUITY_DISCOUNT_RATE = 0.04
ANNUITY_DEFAULT_CASH_RATIO = 0.6


def annuity_present_value(annual: float, years: int, rate: float = ANNUITY_DISCOUNT_RATE) -> float:
    """PV of an annuity-immediate: annual × (1 - (1+r)^-n) / r."""
    if not annual or annual <= 0 or not years or years <= 0:
        return 0.0
    if rate <= 0:
        return annual * years
    return annual * (1 - (1 + rate) ** -years) / rate


def effective_prize_value(tier: dict) -> float:
    """The cash-equivalent value used for EV math.
    Honors explicit cash_value, then PV of (annuity_annual, annuity_years),
    then a default ratio for is_annuity flag, else face prize_amount."""
    face = tier.get("prize_amount") or 0
    if not tier.get("is_annuity"):
        return face
    cv = tier.get("cash_value")
    if cv and cv > 0:
        return cv
    annual = tier.get("annuity_annual")
    years = tier.get("annuity_years")
    if annual and years:
        pv = annuity_present_value(annual, years)
        if pv > 0:
            return pv
    return face * ANNUITY_DEFAULT_CASH_RATIO if face else 0


def calculate_ev(price: float, tiers: list[dict], tickets_remaining: int = None) -> dict:
    """
    tiers: list of {prize_amount, odds_one_in, prizes_remaining, prizes_total,
                    is_annuity?, cash_value?, annuity_annual?, annuity_years?}
    Annuity-marked tiers use effective_prize_value (cash equivalent), not face.
    Returns {ev, return_pct}
    """
    if not tiers or price <= 0:
        return {"ev": None, "return_pct": None}

    use_remaining = (
        tickets_remaining is not None
        and tickets_remaining > 0
        and all(t.get("prizes_remaining") is not None for t in tiers)
    )

    total_ev = 0.0
    for tier in tiers:
        prize = effective_prize_value(tier)
        if not prize or prize <= 0:
            continue

        if use_remaining:
            remaining = tier.get("prizes_remaining", 0) or 0
            if remaining > 0:
                prob = remaining / tickets_remaining
                total_ev += prize * prob
        else:
            odds = tier.get("odds_one_in")
            if odds and odds > 0:
                total_ev += prize / odds

    if total_ev <= 0:
        return {"ev": None, "return_pct": None}

    ev = round(total_ev - price, 4)
    return_pct = round((total_ev / price) * 100, 2)
    return {"ev": ev, "return_pct": return_pct}


def calculate_jackpot_odds(tiers: list[dict], tickets_remaining: int = None) -> float | None:
    """1-in-X odds of winning $1M or more per ticket."""
    big_tiers = [t for t in tiers if (t.get("prize_amount") or 0) >= 1_000_000]
    if not big_tiers:
        return None

    use_remaining = (
        tickets_remaining is not None
        and tickets_remaining > 0
        and all(t.get("prizes_remaining") is not None for t in big_tiers)
    )

    if use_remaining:
        total = sum(t.get("prizes_remaining", 0) or 0 for t in big_tiers)
        if total <= 0:
            return None
        return round(tickets_remaining / total, 1)
    else:
        total_prob = sum(
            1 / t["odds_one_in"]
            for t in big_tiers
            if t.get("odds_one_in") and t["odds_one_in"] > 0
        )
        if total_prob <= 0:
            return None
        return round(1 / total_prob, 1)


def find_top_tier(tiers: list[dict]) -> dict:
    """The headline tier — ranked by cash-equivalent value so for-life prizes
    win ties against same-face cash tiers (e.g. NY's $10K/Wk/Life vs. $10K cash)."""
    if not tiers:
        return {}
    return max(tiers, key=effective_prize_value)


def find_top_prize(tiers: list[dict]) -> tuple[float, int]:
    top = find_top_tier(tiers)
    if not top:
        return 0, None
    return top.get("prize_amount", 0), top.get("prizes_remaining")


def parse_prize_amount(value: str) -> float | None:
    """Parse '$1,000,000' or '1000000' or '$1M' → float"""
    if value is None:
        return None
    value = str(value).strip().replace(",", "").replace("$", "").replace(" ", "")
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if value.upper().endswith(suffix):
            try:
                return float(value[:-1]) * mult
            except ValueError:
                return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_odds(value: str) -> float | None:
    """Parse '1 in 3.50', '1:3.50', '1-in-3.50', or plain '3.50' → 3.50"""
    if value is None:
        return None
    value = str(value).strip().replace(",", "")
    for sep in (" in ", "in ", "-in-", ":"):
        if sep in value.lower():
            parts = value.lower().split(sep)
            try:
                return float(parts[-1].strip())
            except ValueError:
                return None
    try:
        return float(value)
    except ValueError:
        return None
