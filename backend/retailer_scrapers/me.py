"""
Maine State Lottery retailer scraper.

Source: https://www.mainelottery.com/players_info/where_to_buy.html
Endpoint: POST https://www.mainelottery.com/cgi/findAgent.pl  (zipcode=NNNNN)

Returns an HTML table with columns:
    Store Name | Street Address | Town | Zip | Phone | Tickets Sold

Maine ZIP range 03901-04999 is iterated end-to-end; invalid ZIPs return
"No records found" cheaply. Retailers are deduplicated by (name, address, zip).
"""
from __future__ import annotations
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

ENDPOINT = "https://www.mainelottery.com/cgi/findAgent.pl"
ZIP_MIN, ZIP_MAX = 3901, 4999

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.mainelottery.com/players_info/where_to_buy.html",
    "Origin": "https://www.mainelottery.com",
}

WORKERS = 20
TIMEOUT_S = 20
RETRIES = 3

_PHONE_RE = re.compile(r"\(?(\d{3})\)?\s*[-.\s]?\s*(\d{3})\s*[-.\s]?\s*(\d{4})")


def _normalize_phone(raw: str) -> str | None:
    m = _PHONE_RE.search(raw or "")
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if m else (raw or "").strip() or None


def _clean_name(name: str) -> str:
    return (name or "").replace("`", "'").strip()


def _parse_retailers(html: str) -> list[dict]:
    if "No records found" in html:
        return []
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="zebra") or soup.find("table", class_="tbstriped")
    if table is None:
        return []

    out: list[dict] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue

        name = _clean_name(cells[0].get_text(strip=True))
        address = cells[1].get_text(strip=True)
        city = cells[2].get_text(strip=True)
        zip_ = cells[3].get_text(strip=True)
        phone = _normalize_phone(cells[4].get_text(strip=True))

        if not name or not zip_:
            continue
        out.append({
            "external_id": make_external_id(name, address, zip_),
            "name":     name,
            "address":  address or None,
            "city":     city or None,
            "zip_code": zip_,
            "phone":    phone,
            "latitude":  None,
            "longitude": None,
        })
    return out


def _fetch_zip(session: requests.Session, zip_code: str) -> list[dict]:
    for attempt in range(RETRIES):
        try:
            r = session.post(
                ENDPOINT,
                data={"zipcode": zip_code},
                headers=HEADERS,
                timeout=TIMEOUT_S,
            )
            if r.status_code == 200:
                return _parse_retailers(r.text)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                logger.warning("ME zip %s 429; backing off %ds", zip_code, wait)
                time.sleep(wait)
                continue
            logger.debug("ME zip %s HTTP %d", zip_code, r.status_code)
            return []
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.debug("ME zip %s error attempt %d: %s", zip_code, attempt + 1, e)
            if attempt < RETRIES - 1:
                time.sleep(1 + attempt)
    return []


def scrape_me() -> list[dict]:
    zips = [f"{z:05d}" for z in range(ZIP_MIN, ZIP_MAX + 1)]
    logger.info("ME: querying %d ZIPs with %d workers", len(zips), WORKERS)

    seen: set[str] = set()
    retailers: list[dict] = []
    t0 = time.time()
    session = requests.Session()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_fetch_zip, session, z): z for z in zips}
        for fut in as_completed(futures):
            try:
                results = fut.result()
            except Exception as e:
                logger.debug("ME zip %s task error: %s", futures[fut], e)
                continue
            for r in results:
                eid = r["external_id"]
                if eid in seen:
                    continue
                seen.add(eid)
                retailers.append(r)

    logger.info("ME: scraped %d unique retailers in %.0fs", len(retailers), time.time() - t0)
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_me)
    return await upsert_retailers(conn, "ME", retailers)
