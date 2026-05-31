"""
Mississippi Lottery retailer scraper.
Source: WP Google Maps plugin REST endpoint
  GET https://www.mslottery.com/wp-json/wpgmza/v1/markers/

The endpoint ignores its map_id query param and dumps every marker across all
maps (~5,580). Three maps overlap heavily on the same retailers, so we dedupe
on (title, address). Net result: ~3,000 unique MS retailers.
"""
from __future__ import annotations
import logging
import re
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://www.mslottery.com/wp-json/wpgmza/v1/markers/"

_ADDR_RE = re.compile(r"^(.+?),\s*(.+?),\s*MS\s+(\d{5}(?:-\d{4})?)\s*$", re.IGNORECASE)


def _parse_address(addr: str) -> tuple[str | None, str | None, str | None]:
    """Split 'STREET, CITY, MS ZIP' → (street, city, zip)."""
    if not addr:
        return None, None, None
    m = _ADDR_RE.match(addr.strip())
    if not m:
        return addr.strip() or None, None, None
    return m.group(1).strip() or None, m.group(2).strip() or None, m.group(3).strip()


def scrape_ms() -> list[dict]:
    resp = safe_get(API_URL, params={"map_id": "14"})
    if resp is None:
        logger.error("MS: failed to fetch markers endpoint")
        return []
    try:
        markers = resp.json()
    except Exception as e:
        logger.error("MS: invalid JSON: %s", e)
        return []
    if not isinstance(markers, list):
        logger.error("MS: unexpected payload shape: %s", type(markers).__name__)
        return []

    seen: set[tuple] = set()
    retailers: list[dict] = []
    for m in markers:
        if not isinstance(m, dict):
            continue
        title = (m.get("title") or "").strip()
        addr_raw = (m.get("address") or "").strip()
        if not title or not addr_raw:
            continue
        # Skip rows clearly not in MS (a couple junk markers in CA)
        if ", MS " not in addr_raw.upper() and not addr_raw.upper().endswith(" MS"):
            continue

        key = (title.upper(), addr_raw.upper())
        if key in seen:
            continue
        seen.add(key)

        street, city, zip_code = _parse_address(addr_raw)
        lat = lng = None
        try:
            if m.get("lat"): lat = float(m["lat"])
            if m.get("lng"): lng = float(m["lng"])
        except (ValueError, TypeError):
            pass

        ext_id = str(m.get("id") or "").strip()
        if not ext_id:
            from .base import make_external_id
            ext_id = make_external_id(title, addr_raw)

        retailers.append({
            "external_id": ext_id,
            "name": title,
            "address": street,
            "city": city,
            "zip_code": zip_code,
            "phone": None,
            "latitude": lat,
            "longitude": lng,
        })

    logger.info("MS: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_ms)
    return await upsert_retailers(conn, "MS", retailers)
