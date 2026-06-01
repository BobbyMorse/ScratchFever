"""
Ohio Lottery retailer scraper.

The find-a-retailer page on ohiolottery.com calls a JWT-protected JSON API.
Auth credentials are hard-coded in the public Vue bundle (dist/js/app.js) and
serve as the lottery's "mobilepublic" account:

  POST https://authapi-solutions.ohiolottery.com/1.0/Authentication/Login
       json: {"userName": "mobilepublic@mtllc.com", "password": "R7V5Sz8@"}
       → {"data": {"token": "<jwt>", ...}}

  POST https://api-solutions.ohiolottery.com/1.0/Retailer/GetContentElementByFilters
       headers: Authorization: Bearer <jwt>, Content-Type: application/json
       body: {"businessName":"","addressCity":"","county":"<COUNTY>","zip":"",
              "latitude":0,"longitude":0,"ticketCashAmount":0}

A single empty-filter request only returns ~1,500 records out of ~9,800
state-wide, so we sweep Ohio's 88 counties and dedupe by itemID.
Each record carries lat/long, full address, and phone.
"""
from __future__ import annotations
import logging
import time

import requests

from .base import upsert_retailers

logger = logging.getLogger(__name__)

AUTH_URL   = "https://authapi-solutions.ohiolottery.com/1.0/Authentication/Login"
SEARCH_URL = "https://api-solutions.ohiolottery.com/1.0/Retailer/GetContentElementByFilters"
USERNAME   = "mobilepublic@mtllc.com"
PASSWORD   = "R7V5Sz8@"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.ohiolottery.com",
    "Referer": "https://www.ohiolottery.com/",
}

REQUEST_SLEEP = 0.25
REQUEST_TIMEOUT = 45
MAX_RETRIES = 3

# Ohio's 88 counties (uppercase — the API matches exactly on county name)
OH_COUNTIES = [
    "ADAMS", "ALLEN", "ASHLAND", "ASHTABULA", "ATHENS", "AUGLAIZE",
    "BELMONT", "BROWN", "BUTLER", "CARROLL", "CHAMPAIGN", "CLARK",
    "CLERMONT", "CLINTON", "COLUMBIANA", "COSHOCTON", "CRAWFORD",
    "CUYAHOGA", "DARKE", "DEFIANCE", "DELAWARE", "ERIE", "FAIRFIELD",
    "FAYETTE", "FRANKLIN", "FULTON", "GALLIA", "GEAUGA", "GREENE",
    "GUERNSEY", "HAMILTON", "HANCOCK", "HARDIN", "HARRISON", "HENRY",
    "HIGHLAND", "HOCKING", "HOLMES", "HURON", "JACKSON", "JEFFERSON",
    "KNOX", "LAKE", "LAWRENCE", "LICKING", "LOGAN", "LORAIN", "LUCAS",
    "MADISON", "MAHONING", "MARION", "MEDINA", "MEIGS", "MERCER",
    "MIAMI", "MONROE", "MONTGOMERY", "MORGAN", "MORROW", "MUSKINGUM",
    "NOBLE", "OTTAWA", "PAULDING", "PERRY", "PICKAWAY", "PIKE",
    "PORTAGE", "PREBLE", "PUTNAM", "RICHLAND", "ROSS", "SANDUSKY",
    "SCIOTO", "SENECA", "SHELBY", "STARK", "SUMMIT", "TRUMBULL",
    "TUSCARAWAS", "UNION", "VAN WERT", "VINTON", "WARREN", "WASHINGTON",
    "WAYNE", "WILLIAMS", "WOOD", "WYANDOT",
]


def _login(session: requests.Session) -> str | None:
    try:
        resp = session.post(
            AUTH_URL,
            json={"userName": USERNAME, "password": PASSWORD},
            headers={**HEADERS, "Content-Type": "application/json-patch+json"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("OH: auth HTTP %d", resp.status_code)
            return None
        return resp.json().get("data", {}).get("token")
    except Exception as e:
        logger.warning("OH: auth error %s", e)
        return None


def _fetch_county(session: requests.Session, token: str, county: str) -> list[dict]:
    body = {
        "businessName":    "",
        "addressCity":     "",
        "county":          county,
        "zip":             "",
        "latitude":        0,
        "longitude":       0,
        "ticketCashAmount": 0,
    }
    h = {**HEADERS, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.post(SEARCH_URL, json=body, headers=h, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.debug("OH: county=%s HTTP %d", county, resp.status_code)
                continue
            data = resp.json()
            return data.get("data", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.debug("OH: county=%s attempt %d error %s", county, attempt + 1, e)
            time.sleep(2 ** attempt)
    return []


def _normalize(item: dict) -> dict | None:
    name = (item.get("businessName") or "").strip()
    rid  = item.get("itemID")
    if not name or rid is None:
        return None
    state = (item.get("state") or "").strip().upper()
    if state and state != "OH":
        return None

    try:
        lat = float(item["latitude"]) if item.get("latitude") else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(item["longitude"]) if item.get("longitude") else None
    except (TypeError, ValueError):
        lng = None
    # Treat 0/0 (a few records do this) as missing
    if lat == 0 and lng == 0:
        lat = lng = None

    phone = (item.get("phone") or "").strip() or None

    return {
        "external_id": f"oh{rid}",
        "name": name,
        "address": (item.get("address") or "").strip() or None,
        "city": (item.get("city") or "").strip().title() or None,
        "zip_code": (item.get("zip") or "").strip() or None,
        "phone": phone,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_oh() -> list[dict]:
    session = requests.Session()
    token = _login(session)
    if not token:
        logger.warning("OH: could not obtain auth token, skipping")
        return []

    seen: dict[str, dict] = {}
    for i, county in enumerate(OH_COUNTIES):
        rows = _fetch_county(session, token, county)
        before = len(seen)
        for raw in rows:
            r = _normalize(raw)
            if r and r["external_id"] not in seen:
                seen[r["external_id"]] = r
        if (i + 1) % 10 == 0:
            logger.info("OH: %d/%d counties, %d unique retailers",
                        i + 1, len(OH_COUNTIES), len(seen))
        time.sleep(REQUEST_SLEEP)

    logger.info("OH: scraped %d unique retailers", len(seen))
    return list(seen.values())


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_oh)
    return await upsert_retailers(conn, "OH", retailers)
