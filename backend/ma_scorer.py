"""
MA retailer scoring logic — shared by the API and heatmap_generator.
"""
from __future__ import annotations

import csv
import os
from functools import lru_cache

_DIR = os.path.dirname(__file__)
MA_CSV_ENRICHED = os.path.join(_DIR, "..", "ma_retailers_enriched.csv")
MA_CSV_BASE     = os.path.join(_DIR, "..", "ma_retailers.csv")

CHAIN_KEYWORDS = [
    "7-ELEVEN", "7 ELEVEN", "CUMBERLAND FARMS", "CUMBERLAND",
    "MOBIL", "SHELL ", "BP ", "SUNOCO", "GULF ", "GETTY", "CITGO",
    "EXXON", "SPEEDWAY", "CIRCLE K", "WALGREENS", "CVS",
    "SHAWS", "SHAW'S", "STOP & SHOP", "MARKET BASKET", "BIG Y",
    "PRICE RITE", "PRICE CHOPPER", "HANNAFORD", "STAR MARKET",
    "TARGET", "WALMART", "WAL-MART", "COSTCO", "BJ'S", "BJS",
    "DOLLAR GENERAL", "DOLLAR TREE", "FAMILY DOLLAR",
    "RITE AID", "GLOBAL", "NOURIA", "PRIDE", "DUNKIN", "IRVING",
]

TIER_F_CITIES = {
    "METHUEN", "SALISBURY", "SEEKONK", "NORTH ATTLEBOROUGH",
    "ATTLEBORO", "REVERE", "EVERETT", "CHELSEA", "AGAWAM",
    "WEST SPRINGFIELD", "SPRINGFIELD",
}

RURAL_CITIES = {
    "BARRE", "PETERSHAM", "HARDWICK", "PHILLIPSTON", "ROYALSTON",
    "WENDELL", "SHUTESBURY", "LEVERETT", "PELHAM", "WARWICK",
    "HEATH", "ROWE", "CHARLEMONT", "HAWLEY", "BLANDFORD",
    "CHESTER", "MIDDLEFIELD", "WORTHINGTON", "CUMMINGTON",
    "CHESTERFIELD", "GOSHEN", "ASHFIELD", "CONWAY", "SUNDERLAND",
    "MONTAGUE", "ERVING", "GILL", "NORTHFIELD", "ORANGE",
    "NEW SALEM", "OAKHAM", "HUBBARDSTON", "TEMPLETON",
    "WINCHENDON", "GARDNER",
}

TIER_A_CITIES = {
    "WESTFIELD", "WARE", "BELCHERTOWN", "PALMER", "MONSON",
    "STURBRIDGE", "BROOKFIELD", "SPENCER", "LEICESTER", "PAXTON",
    "HOLDEN", "PRINCETON", "GRAFTON", "UPTON", "MENDON",
    "DOUGLAS", "UXBRIDGE", "MILLVILLE", "BLACKSTONE",
    "BERKLEY", "DIGHTON", "SWANSEA", "SOMERSET", "REHOBOTH",
    "PLAINVILLE", "WRENTHAM", "NORFOLK", "MILLIS", "MEDWAY",
    "MEDFIELD", "SHERBORN", "HOLLISTON", "HOPKINTON", "MILFORD",
    "HOPEDALE", "BELLINGHAM", "FRANKLIN", "FOXBORO", "MANSFIELD",
    "NORTON", "RAYNHAM", "TAUNTON", "MIDDLEBORO", "WAREHAM",
    "MATTAPOISETT", "ROCHESTER", "LAKEVILLE", "FREETOWN",
    "FALL RIVER", "NEW BEDFORD", "ACUSHNET", "FAIRHAVEN",
    "DARTMOUTH", "WESTPORT",
}

INDIE_KEYWORDS = [
    "VARIETY", "LIQUOR", "PACKAGE", "SMOKE", "CIGAR", "TOBACCO",
    "DELI", "PIZZA", "CAFE", "CAFE", "BAKERY", "PHARMACY",
    "GENERAL STORE", "COUNTRY STORE", "CORNER STORE", "NEWS",
    "FOOD MART", "MINI MART", "CONVENIENCE", "GROCERY", "SUPERETTE",
]


def _flag(row: dict, key: str) -> bool:
    return str(row.get(key, "")).lower() in ("true", "1", "yes")


def _float(row: dict, key: str) -> float | None:
    try:
        v = row.get(key, "")
        return float(v) if v != "" else None
    except (ValueError, TypeError):
        return None


def _int(row: dict, key: str) -> int | None:
    try:
        v = row.get(key, "")
        return int(v) if v != "" else None
    except (ValueError, TypeError):
        return None


def is_chain(name: str) -> bool:
    n = name.upper()
    return any(kw in n for kw in CHAIN_KEYWORDS)


def score_retailer(row: dict) -> int:
    s = 0
    name = row.get("name", "").upper()
    city = row.get("city", "").upper().strip()

    keno_mon = _flag(row, "kenoMonitor")
    wol_mon  = _flag(row, "wolMonitor")
    self_svc = _flag(row, "selfService")
    chain    = _flag(row, "is_chain_v2") if "is_chain_v2" in row else is_chain(name)
    gas      = _flag(row, "is_gas")

    indie_strength = _int(row, "indie_strength")
    if indie_strength is None:
        indie_kw = any(kw in name for kw in [k.upper() for k in INDIE_KEYWORDS])
        indie_strength = 1 if indie_kw else 0

    # ── Base store signals ─────────────────────────────────────────────────
    if not chain:        s += 20
    if indie_strength >= 1: s += 5
    if indie_strength >= 2: s += 3   # extra indie signal stacking
    if gas:              s -= 5      # gas stations = higher churn traffic
    if not keno_mon:     s += 10
    if not wol_mon:      s += 5
    if not self_svc:     s += 5

    if chain:            s -= 20
    if keno_mon:         s -= 15
    if wol_mon:          s -= 10
    if self_svc:         s -= 10

    # ── Geographic tier (city) ─────────────────────────────────────────────
    if city in RURAL_CITIES:    s += 20
    elif city in TIER_A_CITIES: s += 15
    if city in TIER_F_CITIES:   s -= 25

    # ── ZIP population density ─────────────────────────────────────────────
    density = _float(row, "pop_density")
    if density is not None:
        if density < 300:
            s += 20    # very rural ZIP
        elif density < 1500:
            s += 10    # suburban
        elif density < 5000:
            s += 0     # average
        elif density < 12000:
            s -= 8     # dense urban
        else:
            s -= 15    # very dense urban

    # ── Interstate distance ────────────────────────────────────────────────
    dist = _float(row, "interstate_dist_mi")
    if dist is not None:
        if dist > 8:
            s += 25    # deep interior, few transient players
        elif dist > 4:
            s += 15
        elif dist > 1:
            s += 5
        elif dist > 0.5:
            s -= 5
        else:
            s -= 25    # right on interstate = rest-stop churn

    # ── Google Places signals ──────────────────────────────────────────────
    review_count = _int(row, "review_count")
    if review_count is not None and review_count >= 0:
        if review_count < 20:
            s += 15    # very low volume store
        elif review_count < 80:
            s += 8
        elif review_count < 300:
            s += 0
        elif review_count < 800:
            s -= 8
        else:
            s -= 20    # extremely busy location

    if _flag(row, "is_24h"):
        s -= 10        # 24-hour stores = very high churn
    if _flag(row, "closes_early"):
        s += 5         # closes before 9pm = lower volume
    if _flag(row, "has_lottery_type"):
        s += 5         # Google knows it as a lottery-type store

    return s


_cache: list[dict] | None = None

def load_and_score() -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache
    path = MA_CSV_ENRICHED if os.path.exists(MA_CSV_ENRICHED) else MA_CSV_BASE
    if not os.path.exists(path):
        return []
    retailers = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            s = score_retailer(row)
            retailers.append({
                "id":              row.get("id"),
                "name":            row.get("name", ""),
                "address":         row.get("address", ""),
                "city":            row.get("city", ""),
                "zipCode":         row.get("zipCode", ""),
                "phone":           row.get("phone", ""),
                "latitude":        row.get("latitude"),
                "longitude":       row.get("longitude"),
                "kenoMonitor":     _flag(row, "kenoMonitor"),
                "wolMonitor":      _flag(row, "wolMonitor"),
                "selfService":     _flag(row, "selfService"),
                "kenoType":        row.get("kenoType", ""),
                "games":           row.get("games", ""),
                "isChain":         _flag(row, "is_chain_v2") if "is_chain_v2" in row else is_chain(row.get("name", "")),
                "isGas":           _flag(row, "is_gas"),
                "indieStrength":   _int(row, "indie_strength") or 0,
                "popDensity":      _float(row, "pop_density"),
                "interstateDist":  _float(row, "interstate_dist_mi"),
                "reviewCount":     _int(row, "review_count"),
                "is24h":           _flag(row, "is_24h"),
                "closesEarly":     _flag(row, "closes_early"),
                "score":           s,
            })
    retailers.sort(key=lambda r: -r["score"])
    _cache = retailers
    return retailers
