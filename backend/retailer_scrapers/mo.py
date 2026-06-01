"""
Missouri Lottery retailer scraper.

molottery.com/where-to-play has a plain POST form. We POST a fixed grid of
MO zip codes with radius=45mi and parse the resulting HTML table. Each row:

  <tr>
    <td><a href="https://www.google.com/maps/search/?api=1&query=NAME, ADDR, CITY ST ZIP">NAME</a></td>
    <td>ADDR</td>
    <td>CITY</td>
    <td>Draw Games<br>Scratchers<br>Keno 2 Go<br></td>
  </tr>

The maps query string carries state+zip; we pull both out of it. Dedupe by
name + address since MO returns no native retailer ID.

No lat/long is returned; backfill_retailer_geo.py handles geocoding later.
"""
from __future__ import annotations
import html as html_mod
import logging
import re
import time

import requests

from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

POST_URL = "https://www.molottery.com/where-to-play/where-to-play.do"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
REQUEST_SLEEP = 0.4

# 23-zip grid covering MO. 45-mile radius from each gives full coverage with
# overlap. Picked by metro centers + filling in rural gaps.
MO_ZIPS = [
    # Major metros
    "63101",  # St Louis downtown
    "63017",  # West St Louis
    "64101",  # KC downtown
    "64150",  # KC north
    "64801",  # Joplin
    "65802",  # Springfield
    "65201",  # Columbia
    "65101",  # Jefferson City
    "63501",  # Kirksville
    "63701",  # Cape Girardeau
    "63901",  # Poplar Bluff
    "65301",  # Sedalia
    "65401",  # Rolla
    "65613",  # Bolivar
    "65706",  # Marshfield
    "65775",  # West Plains
    "63935",  # Doniphan
    "64468",  # Maryville
    "64683",  # Trenton
    "64850",  # Neosho
    "65340",  # Marshall
    "63755",  # Perryville
    "65560",  # Salem
]

ROW_RE = re.compile(
    r'<tr>\s*<td>\s*<a href="https://www\.google\.com/maps/search/\?api=1&query=([^"]+)"[^>]*>\s*([^<]+?)\s*</a>\s*</td>'
    r'\s*<td>([^<]+?)</td>\s*<td>([^<]+?)</td>',
    re.S,
)


def _decode(s: str) -> str:
    return html_mod.unescape(s).strip()


def _parse_maps_query(q: str) -> tuple[str | None, str | None]:
    """Extract state and zip from the Google Maps query string."""
    q = html_mod.unescape(q)
    m = re.search(r',\s*([A-Z][A-Za-z .]*?)\s+([A-Z]{2})\s+(\d{5})', q)
    if m:
        return m.group(2), m.group(3)
    return None, None


def _fetch_zip(session: requests.Session, zip_code: str) -> list[dict]:
    data = {
        "city": "",
        "zipcode": zip_code,
        "range": "45",
        "games": "scratchers",
        "parameter": "Search",
    }
    try:
        resp = session.post(POST_URL, data=data, headers=HEADERS, timeout=45)
        if resp.status_code != 200:
            logger.debug("MO: zip=%s HTTP %d", zip_code, resp.status_code)
            return []
    except Exception as e:
        logger.debug("MO: zip=%s error %s", zip_code, e)
        return []

    retailers = []
    for m in ROW_RE.finditer(resp.text):
        query_str, name, address, city = m.groups()
        state, zip5 = _parse_maps_query(query_str)
        if state and state != "MO":
            continue
        name = _decode(name)
        address = _decode(address)
        city = _decode(city)
        if not name or not address:
            continue
        retailers.append({
            "external_id": make_external_id(name, address, zip5 or city),
            "name": name,
            "address": address,
            "city": city or None,
            "zip_code": zip5,
            "phone": None,
            "latitude": None,
            "longitude": None,
        })
    return retailers


def scrape_mo() -> list[dict]:
    session = requests.Session()
    seen: dict[str, dict] = {}

    for i, zip_code in enumerate(MO_ZIPS):
        rows = _fetch_zip(session, zip_code)
        before = len(seen)
        for r in rows:
            if r["external_id"] not in seen:
                seen[r["external_id"]] = r
        logger.info(
            "MO: zip %s (%d/%d) -> %d rows, %d new, %d total",
            zip_code, i + 1, len(MO_ZIPS), len(rows), len(seen) - before, len(seen),
        )
        time.sleep(REQUEST_SLEEP)

    logger.info("MO: scraped %d unique retailers", len(seen))
    return list(seen.values())


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_mo)
    return await upsert_retailers(conn, "MO", retailers)
