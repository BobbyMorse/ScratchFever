"""
Indiana (Hoosier) Lottery retailer scraper.

hoosierlottery.com/where-to-buy responds to ?lat=X&lng=Y query params and
server-renders the 100 nearest retailers as <div class="wtb-card"> blocks.
We sweep a grid across IN and dedupe by name + address + zip.

Card shape (one per retailer):
  <div class="row wtb-card" storeindex="N">
    ...
    <a class="wtb-location-name ...">NAME</a>
    <span class="wtb-card-address">STREET</span>
    <span class="wtb-card-location">CITY, ST ZIP</span>
    ...

No lat/long is returned in the cards; geocoding handled by
backfill_retailer_geo.py via the US Census batch endpoint.
"""
from __future__ import annotations
import html as html_mod
import logging
import re
import time

import requests

from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

LOCATOR_URL = "https://hoosierlottery.com/where-to-buy/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# IN bounding box, padded
IN_LAT_MIN, IN_LAT_MAX = 37.70, 41.85
IN_LNG_MIN, IN_LNG_MAX = -88.15, -84.75
GRID_STEP = 0.30           # ~21 mi N/S, ~16 mi E/W at IN latitude — gives overlap
PAGE_CAP = 100             # server returns at most 100 per request
REQUEST_SLEEP = 0.5
REQUEST_TIMEOUT = 90
MAX_RETRIES = 3

CARD_SPLIT_RE = re.compile(r'<div class="row wtb-card"')
NAME_RE = re.compile(r'wtb-location-name[^"]*">\s*([^<]+)')
ADDR_RE = re.compile(r'wtb-card-address">([^<]+)')
LOC_RE  = re.compile(r'wtb-card-location">([^<]+)')
CITY_STATE_ZIP_RE = re.compile(r'^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})')


def _fetch(session: requests.Session, lat: float, lng: float) -> str | None:
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(
                LOCATOR_URL,
                params={"lat": f"{lat:.4f}", "lng": f"{lng:.4f}"},
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.text
            logger.debug("IN: HTTP %d at (%.4f,%.4f)", resp.status_code, lat, lng)
        except Exception as e:
            logger.debug("IN: (%.4f,%.4f) attempt %d failed: %s", lat, lng, attempt + 1, e)
            time.sleep(2 ** attempt)
    return None


def _parse_cards(html: str) -> list[dict]:
    retailers = []
    chunks = CARD_SPLIT_RE.split(html)
    for chunk in chunks[1:]:
        nm = NAME_RE.search(chunk)
        ad = ADDR_RE.search(chunk)
        lo = LOC_RE.search(chunk)
        if not (nm and ad and lo):
            continue
        name = html_mod.unescape(nm.group(1)).strip()
        address = html_mod.unescape(ad.group(1)).strip()
        loc = html_mod.unescape(lo.group(1)).strip()
        city = state = zip_code = None
        cm = CITY_STATE_ZIP_RE.match(loc)
        if cm:
            city = cm.group("city").strip().title()
            state = cm.group("state")
            zip_code = cm.group("zip")
        if state and state != "IN":
            continue
        if not name or not address:
            continue
        retailers.append({
            "external_id": make_external_id(name, address, zip_code or city or ""),
            "name": name,
            "address": address,
            "city": city,
            "zip_code": zip_code,
            "phone": None,
            "latitude": None,
            "longitude": None,
        })
    return retailers


def scrape_in() -> list[dict]:
    session = requests.Session()
    seen: dict[str, dict] = {}

    lat = IN_LAT_MIN
    total_points = 0
    saturated_points = 0
    while lat <= IN_LAT_MAX:
        lng = IN_LNG_MIN
        while lng <= IN_LNG_MAX:
            total_points += 1
            html = _fetch(session, lat, lng)
            if html:
                cards = _parse_cards(html)
                before = len(seen)
                for r in cards:
                    if r["external_id"] not in seen:
                        seen[r["external_id"]] = r
                if len(cards) >= PAGE_CAP:
                    saturated_points += 1
                if total_points % 25 == 0:
                    logger.info(
                        "IN: %d points scanned, %d unique retailers",
                        total_points, len(seen),
                    )
            time.sleep(REQUEST_SLEEP)
            lng += GRID_STEP
        lat += GRID_STEP

    logger.info(
        "IN: scraped %d unique retailers from %d grid points (%d saturated)",
        len(seen), total_points, saturated_points,
    )
    return list(seen.values())


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_in)
    return await upsert_retailers(conn, "IN", retailers)
