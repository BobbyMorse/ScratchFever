"""
Connecticut Lottery retailer scraper.

The /ajax/getRetailers endpoint filters by `defLat`/`defLng` (geocoded
client-side via Google Maps from whatever the user typed). `retId` MUST be
empty — when it's set to the page id (107262) the endpoint returns a single
default retailer. Response is an HTML fragment with 25 nearest retailers
embedded as `RetailerRepeater_*Label_N` spans and `new google.maps.LatLng(...)`
calls (first LatLng is the search center; remaining N match the N retailers).

Strategy: sweep a lat/lng grid across CT; recursively subdivide cells that hit
the 25-result cap. Deduplicate by hash(name|address|zip).
"""
from __future__ import annotations
import logging
import re
import time
import requests
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://www.ctlottery.org/ajax/getRetailers"
PAGE_URL = "https://www.ctlottery.org/WhereToPlay/107262"
PAGE_CAP = 25  # observed response cap; subdivide below this

# CT bounding box (slightly padded)
LAT_MIN, LAT_MAX = 40.95, 42.10
LNG_MIN, LNG_MAX = -73.75, -71.78
GRID_STEP = 0.10        # ~7 miles
MIN_STEP  = 0.025       # ~1.7 miles — subdivision floor

_NAME_RE  = re.compile(r'RetailerRepeater_RetailerNameLabel_\d+">([^<]+)')
_ADDR_RE  = re.compile(r'RetailerRepeater_RetailerAddressLabel_\d+">([^<]+)')
_CITY_RE  = re.compile(r'RetailerRepeater_RetailerCityLabel_\d+">([^<]+)')
_LATLNG_RE = re.compile(r'new google\.maps\.LatLng\(([\-\d.]+),\s*([\-\d.]+)\)')
_NAME_PREFIX = re.compile(r"^\s*\d+\)\s*")
_CITY_PARSE = re.compile(r"^(.*?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?$")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": PAGE_URL,
        "X-Requested-With": "XMLHttpRequest",
    })
    # Warm session so the endpoint accepts the cookie.
    try:
        s.get(PAGE_URL, timeout=20)
    except Exception as e:
        logger.warning("CT: warmup GET failed: %s", e)
    return s


def _parse_retailers(html: str) -> list[dict]:
    names = _NAME_RE.findall(html)
    addrs = _ADDR_RE.findall(html)
    cities = _CITY_RE.findall(html)
    latlngs = _LATLNG_RE.findall(html)

    # First LatLng is the search-center marker; the rest are retailer pins.
    retailer_latlngs = latlngs[1:] if len(latlngs) >= len(names) + 1 else latlngs

    out: list[dict] = []
    for i, raw_name in enumerate(names):
        name = _NAME_PREFIX.sub("", raw_name).strip()
        if not name:
            continue
        address = (addrs[i].strip() if i < len(addrs) else "") or None
        city_field = (cities[i].strip() if i < len(cities) else "") or ""
        m = _CITY_PARSE.match(city_field)
        if m:
            city, _state, zip_code = m.group(1).strip().upper(), m.group(2), m.group(3)
        else:
            city, zip_code = (city_field or None), None

        lat = lng = None
        if i < len(retailer_latlngs):
            try:
                lat = float(retailer_latlngs[i][0])
                lng = float(retailer_latlngs[i][1])
            except (ValueError, TypeError):
                pass

        out.append({
            "external_id": make_external_id(name, address or "", zip_code or ""),
            "name": name,
            "address": address,
            "city": city,
            "zip_code": zip_code,
            "phone": None,
            "latitude": lat,
            "longitude": lng,
        })
    return out


def _fetch(session: requests.Session, lat: float, lng: float, retries: int = 3) -> list[dict]:
    params = {
        "location": f"{lat:.4f},{lng:.4f}",
        "address": "",
        "pats": "false",
        "htcc": "false",
        "defLat": f"{lat:.6f}",
        "defLng": f"{lng:.6f}",
        "retId": "",
    }
    for attempt in range(retries):
        try:
            r = session.get(API_URL, params=params, timeout=20)
            if r.status_code == 200:
                return _parse_retailers(r.text)
            logger.warning("CT: GET (%.3f,%.3f) HTTP %d", lat, lng, r.status_code)
        except Exception as e:
            logger.warning("CT: GET (%.3f,%.3f) attempt %d failed: %s", lat, lng, attempt + 1, e)
        time.sleep(1.5 ** attempt)
    return []


def _sweep(
    session: requests.Session,
    lat0: float, lng0: float,
    lat1: float, lng1: float,
    step: float,
    seen: set[str],
    out: list[dict],
) -> None:
    """Walk a grid; recursively subdivide cells that hit the per-call cap."""
    lat = lat0
    while lat <= lat1 + 1e-9:
        lng = lng0
        while lng <= lng1 + 1e-9:
            results = _fetch(session, round(lat, 6), round(lng, 6))
            new_count = 0
            for r in results:
                if r["external_id"] not in seen:
                    seen.add(r["external_id"])
                    out.append(r)
                    new_count += 1
            if new_count:
                logger.info("CT (%.3f,%.3f, step=%.3f): %d new (total: %d)",
                            lat, lng, step, new_count, len(out))
            # If the cell is saturated, subdivide a tighter sub-grid.
            if len(results) >= PAGE_CAP and step > MIN_STEP:
                sub = step / 2
                _sweep(session, lat - sub / 2, lng - sub / 2,
                       lat + sub / 2, lng + sub / 2, sub, seen, out)
            time.sleep(0.2)
            lng += step
        lat += step


def scrape_ct() -> list[dict]:
    session = _new_session()
    retailers: list[dict] = []
    seen_ids: set[str] = set()
    _sweep(session, LAT_MIN, LNG_MIN, LAT_MAX, LNG_MAX, GRID_STEP, seen_ids, retailers)
    logger.info("CT: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_ct)
    return await upsert_retailers(conn, "CT", retailers)
