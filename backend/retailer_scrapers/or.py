"""
Oregon Lottery retailer scraper.
API: api2.oregonlottery.org/retailers/Find?PageSize=5000
Requires Ocp-Apim-Subscription-Key header (embedded in the public page JS).
Returns ~3,700 retailers with name, address, city, zip, lat/lng.
"""
from __future__ import annotations
import logging
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://api2.oregonlottery.org/retailers/Find"
API_KEY = "683ab88d339c4b22b2b276e3c2713809"


def _parse_retailer(item: dict) -> dict | None:
    name = (item.get("RetailerName") or "").strip()
    if not name:
        return None
    external_id = str(item.get("RetailerNumber") or "").strip()
    if not external_id:
        return None
    # Skip inactive/closed retailers
    if (item.get("ContractStatus") or "").upper() not in ("", "ACTIVE"):
        return None
    lat = lng = None
    try:
        lat = float(item["Latitude"]) if item.get("Latitude") is not None else None
        lng = float(item["Longitude"]) if item.get("Longitude") is not None else None
    except (ValueError, TypeError):
        pass
    return {
        "external_id": external_id,
        "name": name,
        "address": (item.get("StreetName") or "").strip() or None,
        "city": (item.get("CityName") or "").strip() or None,
        "zip_code": (item.get("ZipCode") or "").strip() or None,
        "phone": (item.get("PhoneNumber") or "").strip() or None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_or() -> list[dict]:
    retailers: list[dict] = []
    seen_ids: set[str] = set()
    page = 1

    while True:
        resp = safe_get(
            API_URL,
            params={"PageSize": 5000, "PageNumber": page},
            headers={
                "Ocp-Apim-Subscription-Key": API_KEY,
                "Referer": "https://www.oregonlottery.org/",
            },
            delay=0.3,
        )
        if resp is None:
            logger.warning("OR: failed to fetch page %d", page)
            break

        try:
            items = resp.json()
        except Exception as e:
            logger.warning("OR: JSON parse error on page %d: %s", page, e)
            break

        if not items or not isinstance(items, list):
            break

        new_count = 0
        for item in items:
            r = _parse_retailer(item)
            if r and r["external_id"] not in seen_ids:
                seen_ids.add(r["external_id"])
                retailers.append(r)
                new_count += 1

        logger.info("OR page %d: %d new (total: %d)", page, new_count, len(retailers))

        if len(items) < 5000:
            break
        page += 1
        if page > 10:
            logger.warning("OR: hit page cap")
            break

    logger.info("OR: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    retailers = scrape_or()
    return await upsert_retailers(conn, "OR", retailers)
