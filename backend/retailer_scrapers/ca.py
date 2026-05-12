"""
California retailer scraper.
API: scalecalservice.calottery.com/api/retailers/near?maxCount=150&latitude=X&longitude=Y
Returns up to 150 retailers per lat/lng query.
Tiled grid covers CA (32.5–42.0°N, 124.5–114.0°W) at 0.25° spacing (~17 miles).
Deduplicates by retailer ID.
"""
from __future__ import annotations
import logging
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://scalecalservice.calottery.com/api/retailers/near"

# CA bounding box
LAT_MIN, LAT_MAX, LAT_STEP = 32.5, 42.0, 0.25
LNG_MIN, LNG_MAX, LNG_STEP = -124.5, -114.0, 0.25
MAX_COUNT = 150


def _grid_points():
    lat = LAT_MIN
    while lat <= LAT_MAX:
        lng = LNG_MIN
        while lng <= LNG_MAX:
            yield round(lat, 4), round(lng, 4)
            lng += LNG_STEP
        lat += LAT_STEP


def _parse_retailer(item: dict) -> dict | None:
    name = (item.get("name") or item.get("pwsDisplayName") or "").strip()
    if not name:
        return None
    external_id = str(item.get("id") or "").strip()
    if not external_id:
        return None
    try:
        lat = float(item["latitude"]) if item.get("latitude") is not None else None
        lng = float(item["longitude"]) if item.get("longitude") is not None else None
    except (ValueError, TypeError):
        lat, lng = None, None
    return {
        "external_id": external_id,
        "name": name,
        "address": (item.get("address1") or "").strip() or None,
        "city": (item.get("city") or "").strip() or None,
        "zip_code": (item.get("zip") or item.get("postalCode") or "").strip() or None,
        "phone": None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_ca() -> list[dict]:
    retailers: list[dict] = []
    seen_ids: set[str] = set()
    points = list(_grid_points())
    logger.info("CA: tiling with %d grid points", len(points))

    for i, (lat, lng) in enumerate(points):
        resp = safe_get(
            API_URL,
            params={"maxCount": MAX_COUNT, "latitude": lat, "longitude": lng},
            delay=0.3,
        )
        if resp is None:
            continue
        try:
            items = resp.json()
            if isinstance(items, dict):
                items = items.get("retailers") or items.get("locations") or []
        except Exception:
            continue

        new_count = 0
        for item in items:
            r = _parse_retailer(item)
            if r and r["external_id"] not in seen_ids:
                seen_ids.add(r["external_id"])
                retailers.append(r)
                new_count += 1

        if new_count:
            logger.info("CA grid [%d/%d] (%.2f,%.2f): %d new (total: %d)",
                        i + 1, len(points), lat, lng, new_count, len(retailers))

    logger.info("CA: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    retailers = scrape_ca()
    return await upsert_retailers(conn, "CA", retailers)
