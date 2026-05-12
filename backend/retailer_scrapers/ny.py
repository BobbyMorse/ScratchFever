"""
New York retailer scraper.
API: nylottery.ny.gov/drupal-api/api/retailers (Drupal view, paginated)
Passing lat=0,lon=0 returns all 13,048 NY retailers across 2,175 pages of 6.
Fields: title, field_street_address, field_city, field_state, field_zipcode, field_geofield
"""
from __future__ import annotations
import logging
from .base import safe_get, make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = (
    "https://nylottery.ny.gov/drupal-api/api/retailers"
    "?_format=json"
    "&latlng[value]=50"
    "&latlng[source_configuration][origin][lat]=0"
    "&latlng[source_configuration][origin][lon]=0"
    "&page={page}"
)


def _parse_retailer(item: dict) -> dict | None:
    name = (item.get("title") or "").strip()
    if not name:
        return None
    address = (item.get("field_street_address") or "").strip()
    city = (item.get("field_city") or "").strip()
    zip_code = (item.get("field_zipcode") or "").strip()
    lat, lng = None, None
    geo = (item.get("field_geofield") or "").strip()
    if geo and "," in geo:
        try:
            parts = geo.split(",")
            lat = float(parts[0].strip())
            lng = float(parts[1].strip())
        except (ValueError, IndexError):
            pass
    return {
        "external_id": make_external_id(name, address, zip_code),
        "name": name,
        "address": address or None,
        "city": city or None,
        "zip_code": zip_code or None,
        "phone": None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_ny() -> list[dict]:
    retailers: list[dict] = []
    seen_ids: set[str] = set()
    page = 0
    total_pages = None

    while True:
        resp = safe_get(API_URL.format(page=page), delay=0.2)
        if resp is None:
            logger.warning("NY: failed to fetch page %d, stopping", page)
            break

        data = resp.json()

        # Drupal REST export returns either a list or an object with "rows"/"pager"
        if isinstance(data, dict):
            items = data.get("rows") or data.get("items") or []
            if total_pages is None:
                pager = data.get("pager") or {}
                total_pages = pager.get("total_pages")
        else:
            items = data  # plain list

        if not items:
            break

        new_this_page = 0
        for item in items:
            r = _parse_retailer(item)
            if r and r["external_id"] not in seen_ids:
                seen_ids.add(r["external_id"])
                retailers.append(r)
                new_this_page += 1

        logger.info("NY page %d: %d new retailers (total so far: %d)", page, new_this_page, len(retailers))

        page += 1
        if total_pages is not None and page >= total_pages:
            break

        # safety cap: NY has ~2175 pages; bail at 3000
        if page >= 3000:
            logger.warning("NY: hit page cap at %d", page)
            break

    logger.info("NY: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    retailers = scrape_ny()
    return await upsert_retailers(conn, "NY", retailers)
