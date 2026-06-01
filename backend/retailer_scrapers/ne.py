"""
Nebraska Lottery retailer scraper.

The NE retailer search at nelottery.com/homeapp/retailers/search is a plain
POST form with three search modes (City / County / Zip). The City dropdown
on the form lists every city that has retailers, so a city-by-city sweep
covers all retailers in the state. The response embeds each retailer as a
<li><p class="bodytext"> block in the format:

    NAME<br/>
    STREET ADDRESS<br/>
    CITY, ST - ZIP<br/>
    County Name<br/>
    PHONE

NE returns no lat/long; geocoding is handled later by backfill_retailer_geo.py
using the US Census batch geocoder.
"""
from __future__ import annotations
import logging
import re
import time

import requests

from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

SEARCH_URL = "https://nelottery.com/homeapp/retailers/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

REQUEST_SLEEP = 0.3
LI_RE = re.compile(
    r'<li>\s*<p class="bodytext">(.*?)</p>\s*</li>', re.S
)
CITY_OPT_RE = re.compile(
    r'<select[^>]+name="retailer_city"[^>]*>(.*?)</select>', re.S
)
OPT_RE = re.compile(r'<option[^>]*>([^<]+)</option>')
CITY_STATE_ZIP_RE = re.compile(
    r'^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s*-\s*(?P<zip>\d{5})$'
)


def _list_cities(session: requests.Session) -> list[str]:
    resp = session.get(SEARCH_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    m = CITY_OPT_RE.search(resp.text)
    if not m:
        return []
    return [c.strip() for c in OPT_RE.findall(m.group(1)) if c.strip()]


def _search_city(session: requests.Session, city: str) -> list[dict]:
    data = {"order_type": "City", "retailer_city": city, "submit": "Submit"}
    try:
        resp = session.post(SEARCH_URL, data=data, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            logger.debug("NE: city=%s HTTP %d", city, resp.status_code)
            return []
    except Exception as e:
        logger.debug("NE: city=%s error %s", city, e)
        return []

    retailers = []
    for m in LI_RE.finditer(resp.text):
        retailer = _parse_block(m.group(1))
        if retailer:
            retailers.append(retailer)
    return retailers


def _parse_block(html: str) -> dict | None:
    parts = [re.sub(r'<[^>]+>', '', p).strip() for p in re.split(r'<br\s*/?>', html)]
    parts = [p for p in parts if p]
    if len(parts) < 3:
        return None

    name = parts[0]
    address = None
    city = state = zip_code = None
    phone = None

    for line in parts[1:]:
        m = CITY_STATE_ZIP_RE.match(line)
        if m:
            city = m.group("city").strip().title()
            state = m.group("state")
            zip_code = m.group("zip")
            continue
        if re.match(r'^[\d\s().+-]{7,}$', line):
            phone = line
            continue
        if line.endswith("County") or line == "County":
            continue
        if address is None:
            address = line

    if state and state != "NE":
        return None
    if not name or not address:
        return None

    return {
        "external_id": make_external_id(name, address, zip_code or ""),
        "name": name,
        "address": address,
        "city": city,
        "zip_code": zip_code,
        "phone": phone,
        "latitude": None,
        "longitude": None,
    }


def scrape_ne() -> list[dict]:
    session = requests.Session()
    cities = _list_cities(session)
    logger.info("NE: %d cities to sweep", len(cities))

    seen: dict[str, dict] = {}
    for i, city in enumerate(cities):
        for r in _search_city(session, city):
            if r["external_id"] not in seen:
                seen[r["external_id"]] = r
        if (i + 1) % 25 == 0:
            logger.info("NE: %d/%d cities, %d unique retailers so far",
                        i + 1, len(cities), len(seen))
        time.sleep(REQUEST_SLEEP)

    logger.info("NE: scraped %d unique retailers", len(seen))
    return list(seen.values())


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_ne)
    return await upsert_retailers(conn, "NE", retailers)
