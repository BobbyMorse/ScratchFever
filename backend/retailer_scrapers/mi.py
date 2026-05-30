"""
Michigan retailer scraper.

Single GraphQL query returns all ~10,600 retailers with lat/lng already populated.
Endpoint and field list discovered from the michiganlottery.com SPA bundle's
`retailersQuery` factory.
"""
from __future__ import annotations
import logging
import requests
from .base import SESSION, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://www.michiganlottery.com/api"
QUERY = "{retailers{id retailerNumber name address city postalCode latitude longitude}}"

# MI bounding box (with small buffer). MI's API occasionally returns the sentinel
# (34.0, -115.0) — Mojave Desert — for retailers it can't geocode; anything outside
# this box is junk.
MI_LAT_RANGE = (41.5, 48.5)
MI_LNG_RANGE = (-90.5, -82.0)


def _parse_retailer(item: dict) -> dict | None:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    external_id = str(item.get("id") or "").strip()
    if not external_id:
        return None
    try:
        lat = float(item["latitude"]) if item.get("latitude") not in (None, "") else None
        lng = float(item["longitude"]) if item.get("longitude") not in (None, "") else None
    except (ValueError, TypeError):
        lat, lng = None, None
    if lat is not None and lng is not None:
        if not (MI_LAT_RANGE[0] <= lat <= MI_LAT_RANGE[1]
                and MI_LNG_RANGE[0] <= lng <= MI_LNG_RANGE[1]):
            lat, lng = None, None
    return {
        "external_id": external_id,
        "name": name,
        "address": (item.get("address") or "").strip() or None,
        "city": (item.get("city") or "").strip() or None,
        "zip_code": (item.get("postalCode") or "").strip() or None,
        "phone": None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_mi() -> list[dict]:
    resp = SESSION.post(
        API_URL,
        json={"query": QUERY},
        headers={
            "Content-Type": "application/json",
            "Origin": "https://www.michiganlottery.com",
            "Referer": "https://www.michiganlottery.com/playing/find-a-retailer",
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    raw = (data.get("data") or {}).get("retailers") or []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        r = _parse_retailer(item)
        if r and r["external_id"] not in seen:
            seen.add(r["external_id"])
            out.append(r)
    logger.info("MI: scraped %d unique retailers", len(out))
    return out


async def run(conn) -> int:
    retailers = scrape_mi()
    return await upsert_retailers(conn, "MI", retailers)
