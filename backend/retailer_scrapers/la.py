"""
Louisiana Lottery retailer scraper.

louisianalottery.com/where-to-play loads its map data from a public
WordPress REST endpoint that supports zip + radius filtering:

  GET /wp-json/la-lotto/v1/retailers?zip=<zip>&keyword=&radius=<miles>

A single call from a centrally located LA zip with a 500-mile radius
returns ~2,960 retailers — effectively the whole state. We use two
opposite-corner zips to be safe and dedupe by retailer key.

Each record has lat/lng in `coords` so no geocoding backfill is needed.
"""
from __future__ import annotations
import logging
import time

import requests

from .base import upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://louisianalottery.com/wp-json/la-lotto/v1/retailers"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Two queries from opposite ends of LA with a 500-mile radius.  A single
# query from any one point seems to return the full state; two is cheap
# insurance against any server-side cap we haven't noticed.
SEED_ZIPS = ["70112", "71101"]   # New Orleans, Shreveport
RADIUS_MI = 500


def _fetch(session: requests.Session, zip_code: str) -> list[dict]:
    try:
        resp = session.get(
            API_URL,
            params={"zip": zip_code, "keyword": "", "radius": RADIUS_MI},
            headers=HEADERS,
            timeout=60,
        )
        if resp.status_code != 200:
            logger.debug("LA: HTTP %d for zip=%s", resp.status_code, zip_code)
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug("LA: fetch zip=%s error %s", zip_code, e)
        return []


def _normalize(item: dict) -> dict | None:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    state = (item.get("state") or "").strip().upper()
    if state and state != "LA":
        return None
    key = str(item.get("key") or "").strip()
    if not key:
        return None

    coords = item.get("coords") or {}
    try:
        lat = float(coords["lat"]) if coords.get("lat") is not None else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(coords["lng"]) if coords.get("lng") is not None else None
    except (TypeError, ValueError):
        lng = None

    address_bits = [
        (item.get("address_1") or "").strip(),
        (item.get("address_2") or "").strip(),
    ]
    address = " ".join(b for b in address_bits if b) or None

    return {
        "external_id": f"la{key}",
        "name": name,
        "address": address,
        "city": (item.get("city") or "").strip().title() or None,
        "zip_code": (item.get("zip") or "").strip() or None,
        "phone": (item.get("phone") or "").strip() or None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_la() -> list[dict]:
    session = requests.Session()
    seen: dict[str, dict] = {}
    for zip_code in SEED_ZIPS:
        items = _fetch(session, zip_code)
        new = 0
        for raw in items:
            r = _normalize(raw)
            if r and r["external_id"] not in seen:
                seen[r["external_id"]] = r
                new += 1
        logger.info("LA: seed zip %s → %d items, %d new (total %d)",
                    zip_code, len(items), new, len(seen))
        time.sleep(0.5)

    logger.info("LA: scraped %d unique retailers", len(seen))
    return list(seen.values())


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_la)
    return await upsert_retailers(conn, "LA", retailers)
