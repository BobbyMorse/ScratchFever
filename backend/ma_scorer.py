"""
MA retailer loader.
"""
from __future__ import annotations

import csv
import os

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
            })
    retailers.sort(key=lambda r: r.get("name", ""))
    _cache = retailers
    return retailers
