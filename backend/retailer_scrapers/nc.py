"""
North Carolina Lottery retailer scraper.
Source: a static JS asset that defines a single `locationsAll` array, one row
per retailer.
  GET https://nclottery.com/Data/WhereToPlay.js.aspx

Row shape (8 elements):
  [name, lat, lng, typeCode, zip, county, address, city]

typeCode (from nclottery.com/WhereToPlay):
  2 = all games   4 = scratch-off + draw   9 = vending  etc.

~7,200 retailers; one network call.
"""
from __future__ import annotations
import json
import logging
import re
from .base import safe_get, make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

URL = "https://nclottery.com/Data/WhereToPlay.js.aspx"

_ARRAY_RE = re.compile(r"locationsAll\s*=\s*(\[.*\])\s*;?\s*$", re.DOTALL)


def scrape_nc() -> list[dict]:
    resp = safe_get(URL, headers={"Accept": "application/javascript, text/javascript, */*;q=0.8"})
    if resp is None:
        logger.error("NC: failed to fetch %s", URL)
        return []

    m = _ARRAY_RE.search(resp.text)
    if not m:
        logger.error("NC: locationsAll assignment not found")
        return []

    try:
        rows = json.loads(m.group(1))
    except Exception as e:
        logger.error("NC: array did not parse as JSON: %s", e)
        return []
    if not isinstance(rows, list):
        return []

    retailers: list[dict] = []
    seen: set[tuple] = set()
    for row in rows:
        if not isinstance(row, list) or len(row) < 8:
            continue
        name, lat, lng, _type, zip_code, _county, address, city = row[:8]
        name = (name or "").strip() if isinstance(name, str) else ""
        if not name:
            continue
        address = (address or "").strip() if isinstance(address, str) else ""
        city = (city or "").strip() if isinstance(city, str) else ""
        zip_str = str(zip_code or "").strip()

        try:
            lat = float(lat) if lat is not None else None
            lng = float(lng) if lng is not None else None
        except (ValueError, TypeError):
            lat = lng = None

        key = (name.upper(), address.upper(), city.upper())
        if key in seen:
            continue
        seen.add(key)

        retailers.append({
            "external_id": make_external_id(name, address, city),
            "name": name,
            "address": address or None,
            "city": city or None,
            "zip_code": zip_str or None,
            "phone": None,
            "latitude": lat,
            "longitude": lng,
        })

    logger.info("NC: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_nc)
    return await upsert_retailers(conn, "NC", retailers)
