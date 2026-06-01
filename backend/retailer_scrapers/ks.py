"""
Kansas Lottery retailer scraper.

playonkansas.com (the new KS lottery portal) calls a public store-locator
API in the background:

  GET https://gateway-web.loyalty.playonkansas.com/services/retailer/api/stores/location
      ?latitude=<lat>&longitude=<lng>&latitudeDelta=<d>&longitudeDelta=<d>

The response is a JSON array of stores with id, storeName, address1, city,
state, zipcode, county, latitude, longitude.  No auth needed.

We sweep a coarse grid across KS using a generous delta and dedupe by id.
"""
from __future__ import annotations
import logging
import time

import requests

from .base import upsert_retailers

logger = logging.getLogger(__name__)

API_URL = (
    "https://gateway-web.loyalty.playonkansas.com"
    "/services/retailer/api/stores/location"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Origin": "https://www.playonkansas.com",
    "Referer": "https://www.playonkansas.com/",
}

# KS bounding box, padded
KS_LAT_MIN, KS_LAT_MAX = 36.85, 40.10
KS_LNG_MIN, KS_LNG_MAX = -102.20, -94.50
GRID_STEP   = 0.50
LAT_DELTA   = 0.30   # ~21 mi
LNG_DELTA   = 0.30
REQUEST_SLEEP = 0.25
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def _fetch(session: requests.Session, lat: float, lng: float) -> list[dict]:
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(
                API_URL,
                params={
                    "latitude":       f"{lat:.4f}",
                    "longitude":      f"{lng:.4f}",
                    "latitudeDelta":  f"{LAT_DELTA}",
                    "longitudeDelta": f"{LNG_DELTA}",
                },
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.debug("KS: HTTP %d at (%.3f,%.3f)", resp.status_code, lat, lng)
                continue
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.debug("KS: (%.3f,%.3f) attempt %d failed: %s", lat, lng, attempt + 1, e)
            time.sleep(2 ** attempt)
    return []


def _normalize(item: dict) -> dict | None:
    name = (item.get("storeName") or "").strip()
    if not name:
        return None
    state = (item.get("state") or "").strip().upper()
    if state and state != "KS":
        return None
    rid = item.get("id")
    if rid is None:
        return None

    addr_bits = [(item.get("address1") or "").strip(), (item.get("address2") or "").strip()]
    address = " ".join(b for b in addr_bits if b) or None

    try:
        lat = float(item["latitude"]) if item.get("latitude") is not None else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(item["longitude"]) if item.get("longitude") is not None else None
    except (TypeError, ValueError):
        lng = None

    return {
        "external_id": f"ks{rid}",
        "name": name,
        "address": address,
        "city": (item.get("city") or "").strip().title() or None,
        "zip_code": (item.get("zipcode") or "").strip() or None,
        "phone": (item.get("phoneNumber") or "").strip() or None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_ks() -> list[dict]:
    session = requests.Session()
    seen: dict[str, dict] = {}

    lat = KS_LAT_MIN
    points = 0
    while lat <= KS_LAT_MAX:
        lng = KS_LNG_MIN
        while lng <= KS_LNG_MAX:
            points += 1
            for raw in _fetch(session, lat, lng):
                r = _normalize(raw)
                if r and r["external_id"] not in seen:
                    seen[r["external_id"]] = r
            if points % 25 == 0:
                logger.info("KS: %d points, %d unique retailers", points, len(seen))
            time.sleep(REQUEST_SLEEP)
            lng += GRID_STEP
        lat += GRID_STEP

    logger.info("KS: scraped %d unique retailers from %d grid points", len(seen), points)
    return list(seen.values())


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_ks)
    return await upsert_retailers(conn, "KS", retailers)
