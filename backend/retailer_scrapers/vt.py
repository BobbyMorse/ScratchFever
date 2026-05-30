"""
Vermont Lottery retailer scraper.
Direct JSON API discovered in vtlottery.com's map.js:
  GET /api/map?city=&lat=&lng=&radius=&agent=1&win=1
Returns the full retailer set (~586) in a single call regardless of radius.
"""
from __future__ import annotations
import logging
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://vtlottery.com/api/map"
# Center of Vermont; radius is effectively ignored by the API.
PARAMS = {"city": "", "lat": "44.0", "lng": "-72.7", "radius": "200", "agent": "1", "win": "1"}

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://vtlottery.com/where-to-play",
}


def scrape_vt() -> list[dict]:
    resp = safe_get(API_URL, headers=HEADERS, params=PARAMS)
    if resp is None:
        logger.error("VT: failed to fetch %s", API_URL)
        return []

    try:
        data = resp.json()
    except Exception as e:
        logger.error("VT: invalid JSON: %s", e)
        return []

    if not isinstance(data, list):
        logger.error("VT: unexpected payload shape: %s", type(data).__name__)
        return []

    retailers: list[dict] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        ext_id = str(item.get("index_id") or "").strip()
        name = (item.get("store_name") or "").strip()
        if not ext_id or not name:
            continue
        if ext_id in seen:
            continue
        seen.add(ext_id)

        lat = lng = None
        try:
            if item.get("lat") not in (None, ""):
                lat = float(item["lat"])
            if item.get("lng") not in (None, ""):
                lng = float(item["lng"])
        except (ValueError, TypeError):
            pass

        retailers.append({
            "external_id": ext_id,
            "name": name,
            "address": (item.get("address") or "").strip() or None,
            "city": (item.get("town") or "").strip() or None,
            "zip_code": (item.get("zip_code") or "").strip() or None,
            "phone": (item.get("phone_number") or "").strip() or None,
            "latitude": lat,
            "longitude": lng,
        })

    logger.info("VT: scraped %d retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_vt)
    return await upsert_retailers(conn, "VT", retailers)
