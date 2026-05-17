"""
Virginia retailer scraper.
API: POST https://www.valottery.com/api/v1/retailers
Strategy: iterate over all VA counties + independent cities, paginate through
each, deduplicate by hashed name+address (no native retailer IDs returned).
No lat/lng in the API response — coordinates left null.
"""
from __future__ import annotations
import logging
import time
import requests
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://www.valottery.com/api/v1/retailers"
PAGE_SIZE = 100

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.valottery.com",
    "Referer": "https://www.valottery.com/aboutus/findaretailer",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# All 95 Virginia counties + 38 independent cities
VA_LOCALITIES = [
    "ACCOMACK", "ALBEMARLE", "ALLEGHANY", "AMELIA", "AMHERST", "APPOMATTOX",
    "ARLINGTON", "AUGUSTA", "BATH", "BEDFORD", "BLAND", "BOTETOURT",
    "BRUNSWICK", "BUCHANAN", "BUCKINGHAM", "CAMPBELL", "CAROLINE", "CARROLL",
    "CHARLES CITY", "CHARLOTTE", "CHESTERFIELD", "CLARKE", "CRAIG", "CULPEPER",
    "CUMBERLAND", "DICKENSON", "DINWIDDIE", "ESSEX", "FAIRFAX", "FAUQUIER",
    "FLOYD", "FLUVANNA", "FRANKLIN", "FREDERICK", "GILES", "GLOUCESTER",
    "GOOCHLAND", "GRAYSON", "GREENE", "GREENSVILLE", "HALIFAX", "HANOVER",
    "HENRICO", "HENRY", "HIGHLAND", "ISLE OF WIGHT", "JAMES CITY",
    "KING AND QUEEN", "KING GEORGE", "KING WILLIAM", "LANCASTER", "LEE",
    "LOUDOUN", "LOUISA", "LUNENBURG", "MADISON", "MATHEWS", "MECKLENBURG",
    "MIDDLESEX", "MONTGOMERY", "NELSON", "NEW KENT", "NORTHAMPTON",
    "NORTHUMBERLAND", "NOTTOWAY", "ORANGE", "PAGE", "PATRICK", "PITTSYLVANIA",
    "POWHATAN", "PRINCE EDWARD", "PRINCE GEORGE", "PRINCE WILLIAM", "PULASKI",
    "RAPPAHANNOCK", "RICHMOND", "ROANOKE", "ROCKBRIDGE", "ROCKINGHAM",
    "RUSSELL", "SCOTT", "SHENANDOAH", "SMYTH", "SOUTHAMPTON", "SPOTSYLVANIA",
    "STAFFORD", "SURRY", "SUSSEX", "TAZEWELL", "WARREN", "WASHINGTON",
    "WESTMORELAND", "WISE", "WYTHE", "YORK",
    # Independent cities
    "ALEXANDRIA", "BRISTOL", "BUENA VISTA", "CHARLOTTESVILLE", "CHESAPEAKE",
    "COLONIAL HEIGHTS", "COVINGTON", "DANVILLE", "EMPORIA", "FAIRFAX CITY",
    "FALLS CHURCH", "FRANKLIN CITY", "FREDERICKSBURG", "GALAX", "HAMPTON",
    "HARRISONBURG", "HOPEWELL", "LEXINGTON", "LYNCHBURG", "MANASSAS",
    "MANASSAS PARK", "MARTINSVILLE", "NEWPORT NEWS", "NORFOLK", "NORTON",
    "PETERSBURG", "POQUOSON", "PORTSMOUTH", "RADFORD", "RICHMOND CITY",
    "ROANOKE CITY", "SALEM", "STAUNTON", "SUFFOLK", "VIRGINIA BEACH",
    "WAYNESBORO", "WILLIAMSBURG", "WINCHESTER",
]


def _post(county: str, page: int) -> dict | None:
    data = {
        "page": str(page),
        "pageSize": str(PAGE_SIZE),
        "zipCode": "",
        "county": county,
        "onlyWithVendingMachines": "false",
    }
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, data=data, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("VA POST county=%s page=%d → HTTP %d", county, page, resp.status_code)
        except Exception as e:
            logger.warning("VA POST county=%s page=%d attempt %d: %s", county, page, attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def _parse(raw: dict, county: str) -> dict | None:
    name = (raw.get("Name") or "").strip()
    address = (raw.get("Street") or "").strip()
    city = (raw.get("City") or "").strip()
    if not name:
        return None
    return {
        "external_id": make_external_id(name, address, city),
        "name": name,
        "address": address or None,
        "city": city or None,
        "zip_code": (raw.get("Zip") or "").strip() or None,
        "phone": None,
        "latitude": None,
        "longitude": None,
    }


def scrape_va() -> list[dict]:
    seen: dict[str, dict] = {}

    for i, county in enumerate(VA_LOCALITIES):
        page = 0
        while True:
            data = _post(county, page)
            if data is None:
                logger.warning("VA: no data for county=%s page=%d — skipping", county, page)
                break

            retailers = data.get("data") or []
            for raw in retailers:
                r = _parse(raw, county)
                if r and r["external_id"] not in seen:
                    seen[r["external_id"]] = r

            total_pages = data.get("totalPages", 1)
            if page + 1 >= total_pages:
                break
            page += 1
            time.sleep(0.2)

        if (i + 1) % 20 == 0:
            logger.info("VA: %d/%d localities done — %d unique retailers so far",
                        i + 1, len(VA_LOCALITIES), len(seen))

        time.sleep(0.15)

    logger.info("VA: scraped %d unique retailers across %d localities",
                len(seen), len(VA_LOCALITIES))
    return list(seen.values())


async def run(conn) -> int:
    retailers = scrape_va()
    return await upsert_retailers(conn, "VA", retailers)
