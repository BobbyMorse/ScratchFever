"""
New Jersey retailer scraper.
API: njlottery.com/api/v1/locations/page (IGT platform, paginated by state)
~6,583 retailers, 20 per page, ~330 pages.
Fields: id, name, address1, city, postalCode, phoneNumber, lattitude, longitude
"""
from __future__ import annotations
import logging
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

BASE_URL = "https://www.njlottery.com"
FIRST_PAGE = "/api/v1/locations/page?size=20&state=NJ&status=Active&page=0&seed=0"


def _parse_retailer(item: dict) -> dict | None:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    external_id = str(item.get("id") or "").strip()
    if not external_id:
        return None
    # note: IGT has a typo "lattitude" in the JSON
    lat = item.get("lattitude") or item.get("latitude")
    lng = item.get("longitude")
    try:
        lat = float(lat) if lat is not None else None
        lng = float(lng) if lng is not None else None
    except (ValueError, TypeError):
        lat, lng = None, None
    phone = (item.get("phoneNumber") or "").strip() or None
    # filter placeholder phone numbers
    if phone in ("9999999999", "0000000000"):
        phone = None
    return {
        "external_id": external_id,
        "name": name,
        "address": (item.get("address1") or "").strip() or None,
        "city": (item.get("city") or "").strip() or None,
        "zip_code": (item.get("postalCode") or "").strip() or None,
        "phone": phone,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_nj() -> list[dict]:
    retailers: list[dict] = []
    seen_ids: set[str] = set()
    next_url = FIRST_PAGE

    while next_url:
        resp = safe_get(BASE_URL + next_url, delay=0.25)
        if resp is None:
            logger.warning("NJ: failed to fetch %s, stopping", next_url)
            break

        data = resp.json()
        items = data.get("locations") or []

        new_this_page = 0
        for item in items:
            r = _parse_retailer(item)
            if r and r["external_id"] not in seen_ids:
                seen_ids.add(r["external_id"])
                retailers.append(r)
                new_this_page += 1

        next_url = data.get("nextPageUrl")
        logger.info("NJ: %d new retailers, next=%s (total: %d)", new_this_page, next_url, len(retailers))

        # safety cap
        if len(retailers) > 20000:
            logger.warning("NJ: hit retailer cap")
            break

    logger.info("NJ: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    retailers = scrape_nj()
    return await upsert_retailers(conn, "NJ", retailers)
