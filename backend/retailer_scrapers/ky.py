"""
Kentucky Lottery retailer scraper.
Source: POST https://www.kylottery.com/webhandlers/CashingAgentsInfo.xhtml
Empty JSON body returns the full retailer list (~3,489) in a single response.

Response shape: {"RETAILERS": [{"NAME","ADDRESS1","ADDRESS2","ADDRESS3",
                                "CITY","ZIP","PHONE","COUNTY","CASHINGTYPE","KENO"}, ...]}
No lat/lng provided; downstream map view will rely on geocoded address.
"""
from __future__ import annotations
import logging
import requests
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

URL = "https://www.kylottery.com/webhandlers/CashingAgentsInfo.xhtml"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "Referer": "https://www.kylottery.com/apps/customer_service/find_retailer.html",
    "X-Requested-With": "XMLHttpRequest",
}


def scrape_ky() -> list[dict]:
    try:
        resp = requests.post(URL, headers=HEADERS, data="{}", timeout=60)
        resp.raise_for_status()
    except Exception as e:
        logger.error("KY: request failed: %s", e)
        return []

    try:
        data = resp.json()
    except Exception as e:
        logger.error("KY: invalid JSON: %s", e)
        return []

    items = data.get("RETAILERS") if isinstance(data, dict) else None
    if not isinstance(items, list):
        logger.error("KY: unexpected payload: %r", type(data).__name__)
        return []

    retailers: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get("NAME") or "").strip()
        if not name:
            continue
        # Address1 is street; address2/3 are usually blank or suite info
        street_parts = [p.strip() for p in (item.get("ADDRESS1"), item.get("ADDRESS2"), item.get("ADDRESS3")) if isinstance(p, str) and p.strip()]
        address = ", ".join(street_parts) or None
        city = (item.get("CITY") or "").strip() or None
        zip_code = (item.get("ZIP") or "").strip() or None
        phone = (item.get("PHONE") or "").strip() or None

        ext_id = make_external_id(name, address or "", city or "", zip_code or "")
        if ext_id in seen:
            continue
        seen.add(ext_id)

        retailers.append({
            "external_id": ext_id,
            "name": name,
            "address": address,
            "city": city,
            "zip_code": zip_code,
            "phone": phone,
            "latitude": None,
            "longitude": None,
        })

    logger.info("KY: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_ky)
    return await upsert_retailers(conn, "KY", retailers)
