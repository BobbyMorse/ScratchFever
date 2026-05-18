"""
Texas Lottery retailer scraper.
POST to texaslottery.com/opencms/Games/Scratch_Offs/Retailer_Locator.jsp with city name.
Fetches all ~1,200 TX cities from the form's select element, then scrapes each.
Returns HTML tables; parsed with stdlib html.parser (no extra deps).
"""
from __future__ import annotations
import html as html_mod
import logging
import re
import time
from html.parser import HTMLParser
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

BASE_URL = "https://www.texaslottery.com"
LOCATOR_URL = BASE_URL + "/opencms/Games/Scratch_Offs/Retailer_Locator.jsp"


class _TableParser(HTMLParser):
    """Extract <td> text from the results table."""
    def __init__(self):
        super().__init__()
        self._in_td = False
        self._cells: list[str] = []
        self._current: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._in_td = True
            self._current = []

    def handle_endtag(self, tag):
        if tag == "td":
            self._cells.append("".join(self._current).strip())
            self._in_td = False

    def handle_data(self, data):
        if self._in_td:
            self._current.append(data)

    def handle_entityref(self, name):
        if self._in_td:
            self._current.append(html_mod.unescape(f"&{name};"))

    def handle_charref(self, name):
        if self._in_td:
            self._current.append(html_mod.unescape(f"&#{name};"))


def _fetch_cities() -> list[str]:
    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
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
    """Parse retailer table rows. Columns: Name, Address, City."""
    parser = _TableParser()
    parser.feed(html)
    cells = parser.cells
    # Every 3 cells = one retailer row (Name, Address, City)
    retailers: list[dict] = []
    i = 0
    while i + 2 < len(cells):
        name = cells[i].strip()
        address = cells[i + 1].strip()
        city = cells[i + 2].strip()
        i += 3
        if not name or name.lower() in ("name", "business name", ""):
            continue
        if not re.search(r"[A-Z]", name):
            continue
        retailers.append({
            "name": name,
            "address": address or None,
            "city": city or None,
            "zip_code": None,
            "phone": None,
            "latitude": None,
            "longitude": None,
            "external_id": make_external_id(name, address, city),
        })
    return retailers


def scrape_tx() -> list[dict]:
    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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

    for i, city in enumerate(cities):
        try:
            resp = session.post(
                LOCATOR_URL,
                data={"submitted": "true", "city": city, "zipcode": "", "distance": "10"},
                timeout=30,
            )
            resp.raise_for_status()
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
            logger.debug("TX: error on city %s: %s", city, e)
        time.sleep(0.3)

        if (i + 1) % 100 == 0:
            logger.info("TX: progress %d/%d cities, %d retailers so far",
                        i + 1, len(cities), len(all_retailers))

    logger.info("TX: scraped %d unique retailers across %d cities",
                len(all_retailers), len(cities))
    return all_retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_tx)
    return await upsert_retailers(conn, "TX", retailers)
