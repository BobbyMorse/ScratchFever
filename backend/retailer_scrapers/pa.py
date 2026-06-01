"""
Pennsylvania Lottery retailer scraper.

palottery.pa.gov exposes a mobile-app JSON endpoint that the public
"Where to Buy" map calls in the background:

  GET /Custom/mobile/geolocate-retailers.aspx
      ?latitude=<lat>&longitude=<lng>&distance=<miles>&inventory=1&monitors=0

Each row carries Location_ID, Location_name, Address, City, Zip, County,
Latitude, Longitude.  Distance is the radius in miles.  We sweep a grid
across PA and dedupe by Location_ID.
"""
from __future__ import annotations
import logging
import time

import requests

from .base import upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://www.palottery.pa.gov/Custom/mobile/geolocate-retailers.aspx"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

# PA bounding box, padded
PA_LAT_MIN, PA_LAT_MAX = 39.65, 42.35
PA_LNG_MIN, PA_LNG_MAX = -80.65, -74.55
GRID_STEP = 0.45     # ~31mi N/S, ~24mi E/W at PA latitude
DISTANCE_MI = 30     # radius per call; chosen to slightly exceed step
REQUEST_SLEEP = 0.3
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def _fetch(session: requests.Session, lat: float, lng: float) -> list[dict]:
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(
                API_URL,
                params={
                    "latitude":  f"{lat:.4f}",
                    "longitude": f"{lng:.4f}",
                    "distance":  str(DISTANCE_MI),
                    "inventory": "1",
                    "monitors":  "0",
                },
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.debug("PA: HTTP %d at (%.3f,%.3f)", resp.status_code, lat, lng)
                continue
            data = resp.json()
            return data.get("Table", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.debug("PA: (%.3f,%.3f) attempt %d failed: %s", lat, lng, attempt + 1, e)
            time.sleep(2 ** attempt)
    return []


def _normalize(item: dict) -> dict | None:
    name = (item.get("Location_name") or "").strip()
    rid  = str(item.get("Location_ID") or "").strip()
    if not name or not rid:
        return None
    try:
        lat = float(item["Latitude"]) if item.get("Latitude") not in (None, "") else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(item["Longitude"]) if item.get("Longitude") not in (None, "") else None
    except (TypeError, ValueError):
        lng = None
    return {
        "external_id": f"pa{rid}",
        "name": name,
        "address": (item.get("Address") or "").strip() or None,
        "city": (item.get("City") or "").strip().title() or None,
        "zip_code": (item.get("Zip") or "").strip() or None,
        "phone": None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_pa() -> list[dict]:
    session = requests.Session()
    seen: dict[str, dict] = {}

    lat = PA_LAT_MIN
    points = 0
    while lat <= PA_LAT_MAX:
        lng = PA_LNG_MIN
        while lng <= PA_LNG_MAX:
            points += 1
            for raw in _fetch(session, lat, lng):
                r = _normalize(raw)
                if r and r["external_id"] not in seen:
                    seen[r["external_id"]] = r
            if points % 25 == 0:
                logger.info("PA: %d points, %d unique retailers", points, len(seen))
            time.sleep(REQUEST_SLEEP)
            lng += GRID_STEP
        lat += GRID_STEP

    logger.info("PA: scraped %d unique retailers from %d grid points", len(seen), points)
    return list(seen.values())


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_pa)
    return await upsert_retailers(conn, "PA", retailers)
