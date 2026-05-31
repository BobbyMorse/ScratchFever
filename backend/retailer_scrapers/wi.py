"""
Wisconsin Lottery retailer scraper.
Source: server-rendered Drupal Views table at
  GET https://wilottery.com/locate-retailers?field_retailer_county_value=<COUNTY>&page=N

50 rows per page; iterate all 72 WI counties with pagination. ~3,400 retailers.
Row HTML has the retailer name in a `<td>` and the address (street / city / county)
in a `<td>` separated by `<br>`. The same address is in the row's Google Maps `<a>`
href: `https://google.com/maps?q=STREET%2CCITY%2CWI`.

No lat/lng available; consumers must geocode by address if needed.
"""
from __future__ import annotations
import logging
import re
from urllib.parse import unquote
from bs4 import BeautifulSoup
from .base import safe_get, make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

URL = "https://wilottery.com/locate-retailers"
MAX_PAGES_PER_COUNTY = 50  # 50×50 = 2,500 rows per county cap; WI's biggest is ~600

WI_COUNTIES = [
    "ADAMS", "ASHLAND", "BARRON", "BAYFIELD", "BROWN", "BUFFALO", "BURNETT",
    "CALUMET", "CHIPPEWA", "CLARK", "COLUMBIA", "CRAWFORD", "DANE", "DODGE",
    "DOOR", "DOUGLAS", "DUNN", "EAU CLAIRE", "FLORENCE", "FOND DU LAC",
    "FOREST", "GRANT", "GREEN", "GREEN LAKE", "IOWA", "IRON", "JACKSON",
    "JEFFERSON", "JUNEAU", "KENOSHA", "KEWAUNEE", "LA CROSSE", "LAFAYETTE",
    "LANGLADE", "LINCOLN", "MANITOWOC", "MARATHON", "MARINETTE", "MARQUETTE",
    "MENOMINEE", "MILWAUKEE", "MONROE", "OCONTO", "ONEIDA", "OUTAGAMIE",
    "OZAUKEE", "PEPIN", "PIERCE", "POLK", "PORTAGE", "PRICE", "RACINE",
    "RICHLAND", "ROCK", "RUSK", "SAINT CROIX", "SAUK", "SAWYER", "SHAWANO",
    "SHEBOYGAN", "TAYLOR", "TREMPEALEAU", "VERNON", "VILAS", "WALWORTH",
    "WASHBURN", "WASHINGTON", "WAUKESHA", "WAUPACA", "WAUSHARA", "WINNEBAGO",
    "WOOD",
]


def _parse_row(tr) -> dict | None:
    tds = tr.find_all("td")
    if len(tds) < 2:
        return None
    # Name is inside a <p> inside an <a>
    name_a = tds[0].find("a")
    name_p = (name_a or tds[0]).find("p")
    name = (name_p.get_text(strip=True) if name_p else tds[0].get_text(strip=True)).strip()
    if not name:
        return None

    # Address cell: STREET<br>CITY<br>COUNTY
    raw = tds[1].get_text("\n", strip=True)
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    street = lines[0] if len(lines) > 0 else None
    city = lines[1] if len(lines) > 1 else None

    return {
        "external_id": make_external_id(name, street or "", city or ""),
        "name": name,
        "address": street,
        "city": city,
        "zip_code": None,
        "phone": None,
        "latitude": None,
        "longitude": None,
    }


def scrape_wi() -> list[dict]:
    retailers: list[dict] = []
    seen: set[str] = set()

    for county in WI_COUNTIES:
        county_total = 0
        for page in range(MAX_PAGES_PER_COUNTY):
            resp = safe_get(URL, params={
                "field_retailer_county_value": county,
                "page": page,
            })
            if resp is None:
                break
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table")
            if not table:
                break
            trs = table.find_all("tr")[1:]  # skip header
            if not trs:
                break

            new_count = 0
            for tr in trs:
                r = _parse_row(tr)
                if r and r["external_id"] not in seen:
                    seen.add(r["external_id"])
                    retailers.append(r)
                    new_count += 1
            county_total += new_count

            # End of pagination — Drupal returns 50 max per page
            if len(trs) < 50:
                break

        if county_total:
            logger.debug("WI: %s — %d retailers (running total: %d)", county, county_total, len(retailers))

    logger.info("WI: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_wi)
    return await upsert_retailers(conn, "WI", retailers)
