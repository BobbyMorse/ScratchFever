"""
Texas Lottery retailer scraper.
POST to texaslottery.com/opencms/Games/Scratch_Offs/Retailer_Locator.jsp with city name.
Fetches all ~1,200 TX cities from the form's select element, then scrapes each.

Each results row has 7 td cells:
    Name | Street Address | City | Phone | Smoking | Self Check | <a>Map</a>
The Map link's href embeds the ZIP (q=<addr>, <city>, Texas, <zip>), which we
extract since the table itself has no ZIP column. There are no lat/lng in the
source — those are filled in afterwards by `backfill_retailer_geo.py`.
"""
from __future__ import annotations
import html as html_mod
import logging
import re
import time
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

BASE_URL = "https://www.texaslottery.com"
LOCATOR_URL = BASE_URL + "/opencms/Games/Scratch_Offs/Retailer_Locator.jsp"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_ROW_RE  = re.compile(r"<tr>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_TBODY_RE = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.DOTALL | re.IGNORECASE)
# Map URL looks like: q=<addr>, <city>, Texas, <zip>
# Anchor on "Texas," so we don't accidentally grab a 5-digit street number.
_ZIP_RE  = re.compile(r"Texas,\s*(\d{5})", re.IGNORECASE)
_TAG_RE  = re.compile(r"<[^>]+>")


def _clean(cell: str) -> str:
    return html_mod.unescape(_TAG_RE.sub("", cell)).strip()


def _fetch_cities() -> list[str]:
    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    try:
        resp = session.get(LOCATOR_URL, timeout=30)
        resp.raise_for_status()
        cities = re.findall(r'<option value="([A-Z][^"]+)"', resp.text)
        cities = [c for c in cities if c not in ("Select One (optional)",)]
        # Remove trailing meta-options like "Yes"/"No"
        cities = [c for c in cities if c.isupper() or any(ch.isspace() for ch in c)]
        return cities
    except Exception as e:
        logger.warning("TX: failed to fetch city list: %s", e)
        return []


def _parse_rows(html: str) -> list[dict]:
    """Parse retailer table rows. 7 cells per row; ZIP comes from the Map href."""
    tbody = _TBODY_RE.search(html)
    body = tbody.group(1) if tbody else html
    retailers: list[dict] = []
    for row_html in _ROW_RE.findall(body):
        cells = _CELL_RE.findall(row_html)
        if len(cells) < 7:
            continue
        name    = _clean(cells[0])
        address = _clean(cells[1])
        city    = _clean(cells[2])
        phone   = _clean(cells[3])
        zm = _ZIP_RE.search(cells[6])
        zip_code = zm.group(1) if zm else None
        if not name or name.lower() in ("retailer name", "name"):
            continue
        if not re.search(r"[A-Z]", name):
            continue
        retailers.append({
            "name": name,
            "address": address or None,
            "city": city or None,
            "zip_code": zip_code,
            "phone": phone or None,
            "latitude": None,
            "longitude": None,
            "external_id": make_external_id(name, address, city, zip_code or ""),
        })
    return retailers


def scrape_tx() -> list[dict]:
    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": _UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": LOCATOR_URL,
    })

    cities = _fetch_cities()
    if not cities:
        logger.warning("TX: no cities found, aborting")
        return []
    logger.info("TX: scraping %d cities", len(cities))

    all_retailers: list[dict] = []
    seen_ids: set[str] = set()
    consecutive_403 = 0
    error_count = 0

    for i, city in enumerate(cities):
        try:
            resp = session.post(
                LOCATOR_URL,
                data={"submitted": "true", "city": city, "zipcode": "", "distance": "10"},
                timeout=30,
            )
            if resp.status_code == 403:
                consecutive_403 += 1
                if consecutive_403 == 1 or consecutive_403 % 50 == 0:
                    logger.warning("TX: HTTP 403 on city %s (consecutive=%d) — IP throttled, backing off 60s",
                                   city, consecutive_403)
                if consecutive_403 >= 5:
                    logger.error("TX: aborting after %d consecutive 403s (banned). Partial result: %d retailers from %d cities",
                                 consecutive_403, len(all_retailers), i)
                    break
                time.sleep(60)
                continue
            resp.raise_for_status()
            consecutive_403 = 0
            rows = _parse_rows(resp.text)
            new_count = 0
            for r in rows:
                if r["external_id"] not in seen_ids:
                    seen_ids.add(r["external_id"])
                    all_retailers.append(r)
                    new_count += 1
            if new_count:
                logger.debug("TX [%d/%d] %s: %d new (total: %d)",
                             i + 1, len(cities), city, new_count, len(all_retailers))
        except Exception as e:
            error_count += 1
            logger.warning("TX: error on city %s: %s", city, e)
        time.sleep(0.5)

        if (i + 1) % 100 == 0:
            logger.info("TX: progress %d/%d cities, %d retailers so far (errors=%d)",
                        i + 1, len(cities), len(all_retailers), error_count)

    logger.info("TX: scraped %d unique retailers across %d cities",
                len(all_retailers), len(cities))
    return all_retailers


async def run(conn) -> int:
    """Scrape TX, upsert new rows, then deactivate stale ones from the old broken parse.

    The pre-fix scraper stored rows with name="Map" / address=<store_name> /
    city=<street_address> and a stable hash of those bogus fields, so the
    re-scraped rows insert under fresh external_ids alongside the old ones.
    After a successful run (guarded by a sanity floor) we drop anything not
    re-touched by this run so the UI doesn't show 5,700 rows for ~2,800 stores.
    """
    import asyncio
    from datetime import datetime, timezone
    run_started = datetime.now(timezone.utc)
    retailers = await asyncio.to_thread(scrape_tx)
    count = await upsert_retailers(conn, "TX", retailers)
    if count >= 1000:
        deleted = await conn.fetchval(
            "WITH d AS (DELETE FROM state_retailers WHERE state_code='TX' AND scraped_at < $1 RETURNING 1) SELECT count(*) FROM d",
            run_started,
        )
        if deleted:
            logger.info("TX: removed %s stale retailer rows from prior scrape", deleted)
    return count
