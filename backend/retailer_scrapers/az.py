"""
Arizona retailer scraper — ports fetch_az_retailers.py into the unified runner.
AZ data upserts into az_retailers (its own table, used by the caller system).
Uses api.arizonalottery.com/v2/retailers with a lat/lng grid (0.28° standard,
0.12° fine grid over Phoenix and Tucson metros).
"""
from __future__ import annotations
import logging
from .base import safe_get

logger = logging.getLogger(__name__)

API_URL = "https://api.arizonalottery.com/v2/retailers"

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.arizonalottery.com",
    "Referer": "https://www.arizonalottery.com/where-to-play/",
}

LAT_MIN, LAT_MAX = 31.33, 37.00
LNG_MIN, LNG_MAX = -114.83, -109.05
GRID_STEP = 0.28
FINE_STEP  = 0.12
PHOENIX_BOX = (33.15, 33.95, -112.50, -111.55)
TUCSON_BOX  = (31.90, 32.45, -111.15, -110.70)
RADIUS = 18
LIMIT  = 40


def _build_grid() -> list[tuple[float, float]]:
    points: set[tuple[float, float]] = set()
    lat = LAT_MIN
    while lat <= LAT_MAX + 0.01:
        lng = LNG_MIN
        while lng <= LNG_MAX + 0.01:
            points.add((round(lat, 4), round(lng, 4)))
            lng += GRID_STEP
        lat += GRID_STEP
    for box in (PHOENIX_BOX, TUCSON_BOX):
        lat_min, lat_max, lng_min, lng_max = box
        lat = lat_min
        while lat <= lat_max + 0.01:
            lng = lng_min
            while lng <= lng_max + 0.01:
                points.add((round(lat, 4), round(lng, 4)))
                lng += FINE_STEP
            lat += FINE_STEP
    return sorted(points, key=lambda p: (-p[0], p[1]))


def _normalize(raw: dict) -> dict | None:
    ext_id = str(raw.get("id") or "").strip()
    name = (raw.get("retailerName") or "").strip()
    if not ext_id or not name:
        return None
    try:
        lat = float(raw["latitude"]) if raw.get("latitude") is not None else None
        lng = float(raw["longitude"]) if raw.get("longitude") is not None else None
    except (ValueError, TypeError):
        lat, lng = None, None
    return {
        "external_id": ext_id,
        "name": name,
        "address": (raw.get("streetAddress") or "").strip() or None,
        "city": (raw.get("city") or "").strip() or None,
        "zip_code": str(raw.get("postalCode") or "").strip() or None,
        "phone": None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_az() -> list[dict]:
    grid = _build_grid()
    logger.info("AZ: %d grid points", len(grid))
    seen: dict[str, dict] = {}

    for i, (lat, lng) in enumerate(grid):
        resp = safe_get(
            API_URL,
            params={"latitude": lat, "longitude": lng, "distance": RADIUS, "limit": LIMIT},
            delay=0.2,
            headers=HEADERS,
        )
        if resp is None:
            continue
        try:
            records = resp.json() or []
        except Exception:
            continue
        for raw in records:
            r = _normalize(raw)
            if r and r["external_id"] not in seen:
                seen[r["external_id"]] = r
        if (i + 1) % 50 == 0:
            logger.info("AZ [%d/%d] unique: %d", i + 1, len(grid), len(seen))

    retailers = list(seen.values())
    logger.info("AZ: scraped %d unique retailers", len(retailers))
    return retailers


async def _upsert_az_retailers(conn, retailers: list[dict]) -> int:
    count = 0
    for r in retailers:
        lat = r.get("latitude")
        lng = r.get("longitude")
        await conn.execute("""
            INSERT INTO az_retailers (id, name, address, city, zip_code, phone, latitude, longitude)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                name      = EXCLUDED.name,
                address   = EXCLUDED.address,
                city      = EXCLUDED.city,
                zip_code  = EXCLUDED.zip_code,
                latitude  = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude
        """,
            r["external_id"], r["name"], r.get("address"), r.get("city"),
            r.get("zip_code"), r.get("phone"), lat, lng,
        )
        count += 1
    return count


async def run(conn) -> int:
    from backend.az_scorer import clear_cache
    retailers = scrape_az()
    count = await _upsert_az_retailers(conn, retailers)
    clear_cache()
    return count
