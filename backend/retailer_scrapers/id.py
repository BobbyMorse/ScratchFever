"""
Idaho Lottery retailer scraper.

idaholottery.com loads the entire retailer dataset as a single JSON file
from S3, then filters client-side. We just fetch that file:

  GET https://id-lottery-public.s3.us-west-2.amazonaws.com/Drupal-Site/Retailers/retailers.json

Returns ~1,360 retailers as a dict keyed by retailer_id; each value carries
name, address_single, city, state, zip, lat, lon, game_types.
"""
from __future__ import annotations
import logging

import requests

from .base import upsert_retailers

logger = logging.getLogger(__name__)

JSON_URL = (
    "https://id-lottery-public.s3.us-west-2.amazonaws.com"
    "/Drupal-Site/Retailers/retailers.json"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def scrape_id() -> list[dict]:
    resp = requests.get(JSON_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    raw = resp.json()
    if not isinstance(raw, dict):
        logger.warning("ID: unexpected payload type %s", type(raw))
        return []

    retailers = []
    for rid, item in raw.items():
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        if (item.get("state") or "").upper() not in ("", "ID"):
            continue

        try:
            lat = float(item["lat"]) if item.get("lat") is not None else None
        except (TypeError, ValueError):
            lat = None
        try:
            lng = float(item["lon"]) if item.get("lon") is not None else None
        except (TypeError, ValueError):
            lng = None

        retailers.append({
            "external_id": str(item.get("retailer_id") or rid),
            "name": name,
            "address": (item.get("address_single") or item.get("address") or "").strip() or None,
            "city": (item.get("city") or "").strip() or None,
            "zip_code": (item.get("zip") or "").strip() or None,
            "phone": None,
            "latitude": lat,
            "longitude": lng,
        })

    logger.info("ID: scraped %d retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_id)
    return await upsert_retailers(conn, "ID", retailers)
