"""
Georgia retailer scraper.
API: galottery.com/api/v1/locations (IGT/AWC platform)
Strategy: fetch page 0 with any zip to get a session seed, then paginate
through all pages using the seed. Returns all ~8,900 GA retailers.
"""
from __future__ import annotations
import logging
import time
from urllib.parse import urlparse, parse_qs
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

BASE_URL = "https://www.galottery.com/api/v1/locations"
PAGE_URL = "https://www.galottery.com/api/v1/locations/page"
PAGE_SIZE = 10

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.galottery.com",
    "Referer": "https://www.galottery.com/en-us/player-zone/where-to-play.html",
}


def _get_seed() -> tuple[str, list[dict]]:
    resp = safe_get(BASE_URL, params={"zip": "30301"}, delay=0, headers=HEADERS)
    if resp is None:
        raise RuntimeError("GA: failed to get session seed")
    d = resp.json()
    seed = parse_qs(urlparse(d.get("nextPageUrl", "")).query).get("seed", [None])[0]
    if not seed:
        raise RuntimeError("GA: could not extract seed from response")
    return seed, d.get("locations", [])


def _parse_retailer(raw: dict) -> dict | None:
    name = (raw.get("name") or "").strip()
    if not name:
        return None
    external_id = str(raw.get("id") or "").strip()
    if not external_id:
        return None
    lat_raw = raw.get("lattitude") or raw.get("latitude")
    lng_raw = raw.get("longitude")
    try:
        lat = float(lat_raw) if lat_raw else None
        lng = float(lng_raw) if lng_raw else None
    except (ValueError, TypeError):
        lat, lng = None, None
    phone = str(raw.get("phoneNumber") or raw.get("phone") or "").strip() or None
    if phone in ("9999999999", "0000000000"):
        phone = None
    return {
        "external_id": external_id,
        "name": name,
        "address": (raw.get("address1") or raw.get("address") or "").strip() or None,
        "city": (raw.get("city") or "").strip() or None,
        "zip_code": str(raw.get("postalCode") or raw.get("zipCode") or "").strip() or None,
        "phone": phone,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_ga() -> list[dict]:
    seed, page0_locs = _get_seed()
    logger.info("GA: seed=%s", seed)

    seen: dict[str, dict] = {}
    for raw in page0_locs:
        r = _parse_retailer(raw)
        if r:
            seen[r["external_id"]] = r

    page = 1
    fail_count = 0

    while True:
        resp = safe_get(
            PAGE_URL,
            params={"zip": "30301", "page": page, "size": PAGE_SIZE, "seed": seed},
            delay=0.12,
            headers=HEADERS,
        )
        if resp is None:
            fail_count += 1
            logger.warning("GA: page %d failed (%d)", page, fail_count)
            if fail_count >= 3:
                try:
                    seed, page0_locs = _get_seed()
                    for raw in page0_locs:
                        r = _parse_retailer(raw)
                        if r and r["external_id"] not in seen:
                            seen[r["external_id"]] = r
                    page = 1
                    fail_count = 0
                    time.sleep(1)
                    continue
                except Exception as e:
                    logger.error("GA: seed refresh failed: %s", e)
                    break
            time.sleep(1)
            continue

        fail_count = 0
        data = resp.json()
        locs = data.get("locations", [])
        for raw in locs:
            r = _parse_retailer(raw)
            if r and r["external_id"] not in seen:
                seen[r["external_id"]] = r

        remaining = data.get("nextItems", 0)
        if page % 100 == 0:
            logger.info("GA page %d: total=%d remaining=%d", page, len(seen), remaining)

        if not locs or remaining == 0:
            logger.info("GA: done at page %d — %d total retailers", page, len(seen))
            break
        page += 1

    retailers = list(seen.values())
    logger.info("GA: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    retailers = scrape_ga()
    return await upsert_retailers(conn, "GA", retailers)
