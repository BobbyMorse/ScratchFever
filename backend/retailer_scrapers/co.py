"""
Colorado Lottery retailer scraper.
API: api.coloradolottery.com/v1/retailers/ — returns all ~3,000 retailers in one call.
Fields: name, address, city, zip_code, phone_number, location (GeoJSON Point).
"""
from __future__ import annotations
import logging
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://api.coloradolottery.com/v1/retailers/"


def _parse_retailer(item: dict) -> dict | None:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    address = (item.get("address") or "").strip() or None
    city = (item.get("city") or "").strip() or None
    zip_code = (item.get("zip_code") or "").strip() or None
    phone = (item.get("phone_number") or "").strip() or None

    lat = lng = None
    loc = item.get("location")
    if isinstance(loc, dict) and loc.get("type") == "Point":
        coords = loc.get("coordinates") or []
        if len(coords) == 2:
            try:
                lng = float(coords[0])
                lat = float(coords[1])
            except (ValueError, TypeError):
                pass

    from .base import make_external_id
    external_id = make_external_id(name, address or "", zip_code or "")

    return {
        "external_id": external_id,
        "name": name,
        "address": address,
        "city": city,
        "zip_code": zip_code,
        "phone": phone,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_co() -> list[dict]:
    resp = safe_get(
        API_URL,
        params={"format": "json"},
        headers={"Referer": "https://www.coloradolottery.com/en/retailers/"},
        delay=0.5,
    )
    if resp is None:
        logger.warning("CO: failed to fetch retailers")
        return []

    try:
        items = resp.json()
    except Exception as e:
        logger.warning("CO: JSON parse error: %s", e)
        return []

    if not isinstance(items, list):
        logger.warning("CO: unexpected response type: %s", type(items))
        return []

    retailers: list[dict] = []
    seen_ids: set[str] = set()
    for item in items:
        r = _parse_retailer(item)
        if r and r["external_id"] not in seen_ids:
            seen_ids.add(r["external_id"])
            retailers.append(r)

    logger.info("CO: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    retailers = scrape_co()
    return await upsert_retailers(conn, "CO", retailers)
