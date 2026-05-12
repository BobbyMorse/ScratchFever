"""
RI retailer loader — reads from Supabase ri_retailers table.
"""
from __future__ import annotations

_cache: list[dict] | None = None

CHAIN_KEYWORDS = [
    "7-ELEVEN", "7 ELEVEN", "CIRCLE K", "CUMBERLAND FARMS", "CUMB FARMS",
    "WALGREENS", "CVS", "WALMART", "WAL-MART", "TARGET", "DOLLAR GENERAL",
    "DOLLAR TREE", "FAMILY DOLLAR", "RITE AID", "SHELL ", "BP ", "CHEVRON",
    "MOBIL", "EXXON", "SUNOCO", "STOP & SHOP", "SHAWS", "SHAW'S",
    "WHOLE FOODS", "TRADER JOE", "MARKET BASKET", "BIG Y",
    "SPEEDWAY", "PILOT ", "LOVES ", "LOVE'S ", "CASEY'S",
    "GULF ", "IRVING ", "GLOBAL ", "CITGO", "SUNOCO",
]


def is_chain(name: str) -> bool:
    n = name.upper()
    return any(kw in n for kw in CHAIN_KEYWORDS)


def _row_to_retailer(row: dict) -> dict:
    name = row.get("name", "")
    return {
        "id":            row.get("id"),
        "name":          name,
        "address":       row.get("address", ""),
        "city":          row.get("city", ""),
        "zipCode":       row.get("zip_code", ""),
        "phone":         row.get("phone", ""),
        "latitude":      row.get("latitude"),
        "longitude":     row.get("longitude"),
        "isChain":       is_chain(name),
        "isGas":         False,
        "indieStrength": 0,
        "popDensity":    None,
        "interstateDist": None,
        "reviewCount":   None,
        "is24h":         False,
        "closesEarly":   False,
    }


async def load_and_score_async(conn) -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache
    rows = await conn.fetch(
        "SELECT id, name, address, city, zip_code, phone, latitude, longitude FROM ri_retailers ORDER BY name"
    )
    retailers = [_row_to_retailer(dict(r)) for r in rows]
    _cache = retailers
    return retailers
