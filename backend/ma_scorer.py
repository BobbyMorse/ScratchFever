"""
MA retailer loader — reads from Supabase ma_retailers table.
"""
from __future__ import annotations

_cache: list[dict] | None = None

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


def is_chain(name: str) -> bool:
    n = name.upper()
    return any(kw in n for kw in CHAIN_KEYWORDS)


def _row_to_retailer(row: dict) -> dict:
    name = row.get("name", "")
    return {
        "id":             str(row.get("id", "")),
        "name":           name,
        "address":        row.get("address", ""),
        "city":           row.get("city", ""),
        "zipCode":        row.get("zip_code", ""),
        "phone":          row.get("phone", ""),
        "latitude":       row.get("latitude"),
        "longitude":      row.get("longitude"),
        "kenoMonitor":    bool(row.get("keno_monitor")),
        "wolMonitor":     bool(row.get("wol_monitor")),
        "selfService":    bool(row.get("self_service")),
        "kenoType":       row.get("keno_type", ""),
        "games":          row.get("games", ""),
        "isChain":        bool(row.get("is_chain")) if row.get("is_chain") is not None else is_chain(name),
        "isGas":          bool(row.get("is_gas")),
        "indieStrength":  row.get("indie_strength") or 0,
        "popDensity":     row.get("pop_density"),
        "interstateDist": row.get("interstate_dist_mi"),
        "reviewCount":    row.get("review_count"),
        "is24h":          bool(row.get("is_24h")),
        "closesEarly":    bool(row.get("closes_early")),
    }


async def load_and_score_async(conn) -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache
    rows = await conn.fetch(
        """SELECT id, name, address, city, zip_code, phone, latitude, longitude,
                  keno_monitor, wol_monitor, self_service, keno_type, games,
                  is_chain, is_gas, indie_strength, pop_density,
                  interstate_dist_mi, review_count, is_24h, closes_early
           FROM ma_retailers
           WHERE is_active = TRUE
           ORDER BY name"""
    )
    retailers = [_row_to_retailer(dict(r)) for r in rows]
    _cache = retailers
    return retailers


def clear_cache():
    global _cache
    _cache = None


# Legacy sync shim — kept so nothing breaks if called during import
def load_and_score() -> list[dict]:
    return _cache or []
