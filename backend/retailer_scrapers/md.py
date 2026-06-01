"""
Maryland Lottery retailer scraper.

rewards.mdlottery.com/retail/locator returns JSON when POSTed with the
right XHR headers; otherwise the same URL returns the HTML page shell.
The request body is form-encoded: Latitude / Longitude / Radius / Zipcode.

Response:
  { "Locations": [
      { "Address1": "...", "City": "...", "State": "MD", "Zipcode": "...",
        "PrimaryPhone": "...", "ExternalLocationID": "...",
        "LocationName": "...", "Latitude": <float>, "Longitude": <float> },
      ...
  ] }

We sweep a grid of MD zip codes with a 25-mile radius and dedupe by
ExternalLocationID.
"""
from __future__ import annotations
import logging
import time

import requests

from .base import upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://rewards.mdlottery.com/retail/locator"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://rewards.mdlottery.com/retail/locator",
}

# Grid of MD-representative zips with 25-mi radius
MD_ZIPS = [
    "21201",  # Baltimore inner
    "21228",  # Catonsville
    "21209",  # Pikesville
    "21044",  # Columbia
    "20783",  # Hyattsville
    "20850",  # Rockville
    "21701",  # Frederick
    "21502",  # Cumberland
    "21541",  # Oakland (far west)
    "21601",  # Easton (Eastern Shore mid)
    "21801",  # Salisbury (lower Eastern Shore)
    "21842",  # Ocean City
    "21401",  # Annapolis
    "20619",  # Lexington Park
    "21788",  # Thurmont
    "21130",  # Perryville
    "20705",  # Beltsville
]
RADIUS_MI = 25
REQUEST_SLEEP = 0.4
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


def _fetch(session: requests.Session, zip_code: str) -> list[dict]:
    body = {
        "Latitude":  "0",
        "Longitude": "0",
        "Radius":    str(RADIUS_MI),
        "Zipcode":   zip_code,
    }
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.post(API_URL, data=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.debug("MD: zip=%s HTTP %d", zip_code, resp.status_code)
                continue
            data = resp.json()
            return data.get("Locations", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.debug("MD: zip=%s attempt %d error %s", zip_code, attempt + 1, e)
            time.sleep(2 ** attempt)
    return []


def _normalize(item: dict) -> dict | None:
    name = (item.get("LocationName") or "").strip()
    rid  = str(item.get("ExternalLocationID") or "").strip()
    if not name or not rid:
        return None
    state = (item.get("State") or "").strip().upper()
    if state and state != "MD":
        return None

    addr_bits = [
        (item.get("Address1") or "").strip(),
        (item.get("Address2") or "").strip(),
    ]
    address = " ".join(b for b in addr_bits if b) or None

    try:
        lat = float(item["Latitude"]) if item.get("Latitude") is not None else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(item["Longitude"]) if item.get("Longitude") is not None else None
    except (TypeError, ValueError):
        lng = None

    phone = (item.get("PrimaryPhone") or "").strip() or None
    return {
        "external_id": f"md{rid}",
        "name": name,
        "address": address,
        "city": (item.get("City") or "").strip().title() or None,
        "zip_code": (item.get("Zipcode") or "").strip() or None,
        "phone": phone,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_md() -> list[dict]:
    session = requests.Session()
    seen: dict[str, dict] = {}
    for i, zip_code in enumerate(MD_ZIPS):
        rows = _fetch(session, zip_code)
        before = len(seen)
        for raw in rows:
            r = _normalize(raw)
            if r and r["external_id"] not in seen:
                seen[r["external_id"]] = r
        logger.info("MD: zip %s (%d/%d) → %d rows, %d new (total %d)",
                    zip_code, i + 1, len(MD_ZIPS), len(rows), len(seen) - before, len(seen))
        time.sleep(REQUEST_SLEEP)

    logger.info("MD: scraped %d unique retailers", len(seen))
    return list(seen.values())


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_md)
    return await upsert_retailers(conn, "MD", retailers)
