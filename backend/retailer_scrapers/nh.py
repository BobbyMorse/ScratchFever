"""
New Hampshire Lottery retailer scraper.

The NH lottery's find-retailer page calls a third-party API at
gambytservices.com via a hardcoded API key embedded in the page's JS bundle.
We replay those calls directly — no Playwright required.

  GET https://prod.game-data.gambytservices.com/v1/locations/geo/{lat}/{lng}?radius={miles}
    headers: x-api-key, x-client-id, referer

The API caps each response at 100 results (sorted by distance), so we sweep
a grid of points across NH and subdivide any cell that returns the cap.
Returned records include lat/long, so retailers come back geocoded.
"""
from __future__ import annotations
import logging
import time

import requests

from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

API_BASE = "https://prod.game-data.gambytservices.com/v1/locations/geo"
API_KEY  = "1c4c69db-274c-4f59-95c5-3211cd74e9d8"
CLIENT_ID = "nh-portal-server"

HEADERS = {
    "x-api-key":   API_KEY,
    "x-client-id": CLIENT_ID,
    "referer":     "https://www.nhlottery.com/",
    "user-agent":  (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# NH bounding box, padded slightly so a grid with overlap covers the border.
NH_LAT_MIN, NH_LAT_MAX = 42.65, 45.35
NH_LNG_MIN, NH_LNG_MAX = -72.65, -70.55

PAGE_CAP        = 100   # API hard-cap per response
INITIAL_RADIUS  = 15    # miles
INITIAL_STEP    = 0.20  # degrees (~14 miles N/S, ~10 mi E/W at this latitude)
MIN_STEP        = 0.025 # ~1.7 miles — recursion floor
REQUEST_SLEEP   = 0.15  # seconds between calls (be polite)


def _fetch(lat: float, lng: float, radius: int, session: requests.Session) -> list[dict]:
    url = f"{API_BASE}/{lat:.4f}/{lng:.4f}?radius={radius}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            logger.debug("NH: HTTP %d at %s", resp.status_code, url)
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug("NH: request error at %s: %s", url, e)
        return []


def _sweep(seen: dict[str, dict], session: requests.Session,
           lat_min: float, lat_max: float, lng_min: float, lng_max: float,
           step: float, radius: int) -> None:
    """Walk a grid; recurse into cells that hit the 100-result cap."""
    lat = lat_min
    while lat <= lat_max:
        lng = lng_min
        while lng <= lng_max:
            results = _fetch(lat, lng, radius, session)
            new = 0
            for item in results:
                rid = item.get("id")
                if rid and rid not in seen:
                    seen[rid] = item
                    new += 1

            # Hit the cap → there may be more retailers we missed. Subdivide.
            if len(results) >= PAGE_CAP and step > MIN_STEP:
                half = step / 2
                logger.debug(
                    "NH: cap hit at (%.3f,%.3f) r=%d — subdividing", lat, lng, radius
                )
                _sweep(
                    seen, session,
                    lat - half, lat + half,
                    lng - half, lng + half,
                    step=half, radius=max(3, radius // 2),
                )

            time.sleep(REQUEST_SLEEP)
            lng += step
        lat += step


def scrape_nh() -> list[dict]:
    session = requests.Session()
    seen: dict[str, dict] = {}

    _sweep(
        seen, session,
        NH_LAT_MIN, NH_LAT_MAX,
        NH_LNG_MIN, NH_LNG_MAX,
        step=INITIAL_STEP, radius=INITIAL_RADIUS,
    )

    retailers: list[dict] = []
    for item in seen.values():
        # Filter to NH only (border zips around 30mi from Manchester can be MA/ME/VT)
        if (item.get("region") or "").upper() != "NH":
            continue
        r = _normalize(item)
        if r:
            retailers.append(r)

    logger.info("NH: scraped %d unique retailers", len(retailers))
    return retailers


def _normalize(item: dict) -> dict | None:
    name = (item.get("name") or "").strip()
    if not name:
        return None

    external_id = str(item.get("id") or "").strip()
    if not external_id:
        external_id = make_external_id(
            name,
            item.get("address1") or "",
            item.get("postalCode") or "",
        )

    lat = lng = None
    try:
        lat = float(item["lat"]) if item.get("lat") is not None else None
        lng = float(item["long"]) if item.get("long") is not None else None
    except (ValueError, TypeError):
        pass

    return {
        "external_id": external_id,
        "name":        name,
        "address":     (item.get("address1") or "").strip() or None,
        "city":        (item.get("city") or "").strip() or None,
        "zip_code":    (item.get("postalCode") or "").strip() or None,
        "phone":       (item.get("phone") or "").strip() or None,
        "latitude":    lat,
        "longitude":   lng,
    }


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_nh)
    return await upsert_retailers(conn, "NH", retailers)
