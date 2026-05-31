"""
Oklahoma Lottery retailer scraper.
Source: GET https://www.lottery.ok.gov/retailers-search?Criteria=<text>

The endpoint substring-matches Criteria against name+city+zip. An empty Criteria
returns []. Iterating OK's 3-digit ZIP prefixes (730–749) covers all ~1,843
unique retailers in one pass.
"""
from __future__ import annotations
import logging
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

URL = "https://www.lottery.ok.gov/retailers-search"
OK_ZIP_PREFIXES = list(range(730, 750))  # OK ZIP codes start with 73x or 74x

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.lottery.ok.gov/retailers/find",
}


def _parse(item: dict) -> dict | None:
    name = (item.get("Name") or "").strip()
    if not name:
        return None
    ext_id = str(item.get("Id") or item.get("RetailerNo") or "").strip()
    if not ext_id:
        return None
    addr1 = (item.get("Addr1") or "").strip().rstrip(",").strip() or None
    city = (item.get("City") or "").strip() or None
    zip_code = (item.get("Zip") or "").strip() or None

    lat = lng = None
    try:
        if item.get("Latitude") is not None: lat = float(item["Latitude"])
        if item.get("Longitude") is not None: lng = float(item["Longitude"])
    except (ValueError, TypeError):
        pass
    if lat == 0 and lng == 0:
        lat = lng = None

    return {
        "external_id": ext_id,
        "name": name,
        "address": addr1,
        "city": city,
        "zip_code": zip_code,
        "phone": None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_ok() -> list[dict]:
    retailers: list[dict] = []
    seen: set[str] = set()

    for prefix in OK_ZIP_PREFIXES:
        resp = safe_get(URL, params={"Criteria": str(prefix)}, headers=HEADERS)
        if resp is None:
            continue
        try:
            data = resp.json()
        except Exception:
            continue
        items = data.get("Retailers") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            r = _parse(item)
            if r and r["external_id"] not in seen:
                seen.add(r["external_id"])
                retailers.append(r)

    logger.info("OK: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_ok)
    return await upsert_retailers(conn, "OK", retailers)
