"""
Fetch all Rhode Island Lottery retailers and save to ri_retailers.csv.

Strategy:
  1. Boot Playwright once to load the retailer page and intercept the AWC API
     call — this captures the x-esa-api-key auth header.
  2. Use plain requests with that key to iterate every RI city/town, paginating
     through all results for each, then deduplicate by retailer ID.

Output: ri_retailers.csv
"""
import csv
import time
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH    = Path(__file__).parent / "ri_retailers.csv"
SEED_URL    = "https://www.rilot.com/en-us/player-zone/find-a-retailer.html"
AWC_BASE    = "https://ris.p1.awc.lotteryservices.net"
LOC_PATH    = "/api/v1/locations"
PAGE_PATH   = "/api/v1/locations/page"
PAGE_SIZE   = 10   # Must match the initial fetch size so the seed-based offset is correct

FIELDNAMES = ["id", "name", "address", "city", "state", "zipCode", "phone", "latitude", "longitude"]

# All RI cities/towns (official municipalities + common alternate names in lottery DB)
RI_CITIES = [
    "Barrington", "Bristol", "Burrillville", "Central Falls", "Charlestown",
    "Coventry", "Cranston", "Cumberland", "East Greenwich", "East Providence",
    "Exeter", "Foster", "Glocester", "Hopkinton", "Jamestown", "Johnston",
    "Lincoln", "Little Compton", "Middletown", "Narragansett", "Newport",
    "New Shoreham", "North Kingstown", "North Providence", "North Smithfield",
    "Pawtucket", "Portsmouth", "Providence", "Richmond", "Scituate",
    "Smithfield", "South Kingstown", "Tiverton", "Warren", "Warwick",
    "West Greenwich", "West Warwick", "Westerly", "Woonsocket",
    # Villages / alternate names that sometimes appear in retail databases
    "Wakefield", "Peace Dale", "Wyoming", "Hope Valley", "Carolina",
    "Shannock", "Wood River Junction", "Ashaway", "Bradford",
    "Harmony", "Chepachet", "Harrisville", "Oakland", "Mapleville",
    "Pascoag", "Nasonville", "Greenville", "Georgiaville", "Slatersville",
    "Primrose", "Forestdale", "Manville", "Albion", "Lonsdale",
    "Berkeley", "Valley Falls", "Ashton", "Centerdale", "Fruit Hill",
    "Knightsville", "Natick", "Phenix", "Pontiac", "Western Cranston",
    "Apponaug", "Arctic", "Centreville", "Crompton", "Riverpoint",
    "Hillsgrove", "Norwood", "Pawtuxet", "Conimicut", "Hoxsie",
    "Buttonwoods", "Aldrich", "Cowesett", "East Warwick", "Pilgrim Park",
    "Potowomut", "Remington", "Sandy Lane", "Shawomet",
    "Attleboro Falls",   # near border, sometimes RI zip
]

# RI ZIP codes — comprehensive list; we use these only if city iteration misses things
RI_ZIPS = [
    "02801", "02802", "02804", "02806", "02807", "02808", "02809",
    "02812", "02813", "02814", "02815", "02816", "02817", "02818",
    "02822", "02823", "02824", "02825", "02826", "02827", "02828",
    "02829", "02830", "02831", "02832", "02833", "02835", "02836",
    "02837", "02838", "02839", "02840", "02841", "02842",
    "02860", "02861", "02863", "02864", "02865",
    "02871", "02872", "02873", "02874", "02875", "02876", "02877",
    "02878", "02879", "02880", "02881", "02882", "02883", "02884", "02885",
    "02886", "02888", "02889",
    "02891", "02892", "02893", "02894", "02895", "02896", "02898",
    "02901", "02902", "02903", "02904", "02905", "02906", "02907",
    "02908", "02909", "02910", "02911", "02912", "02914", "02915",
    "02916", "02917", "02919", "02920", "02921",
]


def get_api_key() -> str:
    """Load the retailer page once via Playwright; intercept the x-esa-api-key header."""
    from playwright.sync_api import sync_playwright

    api_key = [None]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = ctx.new_page()

        def handle_route(route, request):
            if LOC_PATH in request.url and api_key[0] is None:
                key = request.headers.get("x-esa-api-key")
                if key:
                    api_key[0] = key
                    logger.info("Got x-esa-api-key: %s...", key[:12])
            try:
                route.continue_()
            except Exception:
                pass

        page.route("**/*", handle_route)
        try:
            page.goto(SEED_URL, wait_until="networkidle", timeout=30_000)
        except Exception as e:
            logger.warning("Page load warning: %s", e)
        browser.close()

    if not api_key[0]:
        raise RuntimeError("Could not capture x-esa-api-key from RI lottery page")
    return api_key[0]


def make_session(api_key: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US",
        "Origin": "https://www.rilot.com",
        "Referer": "https://www.rilot.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "x-user-agent": "portal",
        "x-esa-api-key": api_key,
    })
    return s


def fetch_page(session: requests.Session, url: str, params: dict) -> dict | None:
    try:
        r = session.get(url, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        logger.debug("HTTP %d for %s %s", r.status_code, url, params)
    except Exception as e:
        logger.debug("Request error: %s", e)
    return None


def fetch_city(session: requests.Session, city: str) -> list[dict]:
    """Fetch all retailers for a given city, following pagination."""
    retailers = []

    # Initial page
    data = fetch_page(session, f"{AWC_BASE}{LOC_PATH}", {"city": city})
    if not data:
        return []

    retailers.extend(data.get("locations", []))
    next_items = data.get("nextItems", 0)
    next_url = data.get("nextPageUrl", "")

    if not next_url or next_items == 0:
        return retailers

    # Extract seed from nextPageUrl
    pqs = parse_qs(urlparse(next_url).query)
    seed = pqs.get("seed", [None])[0]
    if not seed:
        return retailers

    # Paginate: page index starts at 1 for /page endpoint
    page = 1
    while True:
        pdata = fetch_page(
            session, f"{AWC_BASE}{PAGE_PATH}",
            {"city": city, "page": page, "size": PAGE_SIZE, "seed": seed},
        )
        if not pdata:
            break
        locs = pdata.get("locations", [])
        retailers.extend(locs)
        if not locs or pdata.get("nextItems", 0) == 0:
            break
        page += 1
        time.sleep(0.1)

    return retailers


def normalize(raw: dict) -> dict:
    return {
        "id":        str(raw.get("id", "")),
        "name":      (raw.get("name") or "").strip().title(),
        "address":   (raw.get("address1") or "").strip().title(),
        "city":      (raw.get("city") or "").strip().title(),
        "state":     "RI",
        "zipCode":   str(raw.get("postalCode") or "").strip(),
        "phone":     str(raw.get("phoneNumber") or "").strip(),
        "latitude":  str(raw.get("lattitude") or ""),   # note: typo in API
        "longitude": str(raw.get("longitude") or ""),
    }


def main():
    logger.info("Getting API key via Playwright...")
    api_key = get_api_key()
    session = make_session(api_key)

    seen: dict[str, dict] = {}
    total_queries = 0

    # Phase 1: iterate all RI cities/towns
    logger.info("Querying %d cities/towns...", len(RI_CITIES))
    for city in RI_CITIES:
        locs = fetch_city(session, city)
        total_queries += 1
        new = 0
        for raw in locs:
            rid = raw.get("id")
            if rid and rid not in seen:
                seen[rid] = normalize(raw)
                new += 1
        if locs:
            logger.info("  %-25s → %3d total, %3d new  (running: %d)", city, len(locs), new, len(seen))
        time.sleep(0.15)

    # Phase 2: query by ZIP code — catches any retailers in unrecognized city names
    logger.info("\nQuerying %d ZIP codes to catch stragglers...", len(RI_ZIPS))
    zip_new = 0
    for zip_code in RI_ZIPS:
        data = fetch_page(session, f"{AWC_BASE}{LOC_PATH}", {"zip": zip_code})
        # Also try postalCode param
        if not data:
            data = fetch_page(session, f"{AWC_BASE}{LOC_PATH}", {"postalCode": zip_code})
        if data:
            for raw in data.get("locations", []):
                rid = raw.get("id")
                if rid and rid not in seen:
                    seen[rid] = normalize(raw)
                    zip_new += 1
        time.sleep(0.1)

    logger.info("ZIP sweep found %d additional retailers", zip_new)
    logger.info("Total unique retailers: %d (from %d queries)", len(seen), total_queries)

    records = [r for r in seen.values() if r["name"]]
    records.sort(key=lambda r: (r["city"], r["name"]))

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)

    logger.info("Saved %d retailers → %s", len(records), OUT_PATH)


if __name__ == "__main__":
    main()
