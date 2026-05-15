"""
Illinois Lottery retailer scraper.
Uses Playwright to discover the store-locator API by navigating to
https://www.illinoislottery.com/store-locator, entering a zip code,
and intercepting the XHR/fetch call. Then makes direct HTTP requests
for remaining IL zip codes.
"""
from __future__ import annotations
import re
import logging
import time
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

LOCATOR_URL = "https://www.illinoislottery.com/store-locator"

# Representative IL zip codes covering all regions of the state
IL_ZIPS = [
    # Chicago & Cook County
    "60601", "60602", "60603", "60604", "60605", "60606", "60607", "60608",
    "60609", "60610", "60611", "60612", "60613", "60614", "60615", "60616",
    "60617", "60618", "60619", "60620", "60621", "60622", "60623", "60624",
    "60625", "60626", "60628", "60629", "60630", "60631", "60632", "60633",
    "60634", "60636", "60637", "60638", "60639", "60640", "60641", "60642",
    "60643", "60644", "60645", "60646", "60647", "60649", "60651", "60652",
    "60653", "60654", "60655", "60656", "60657", "60659", "60660",
    # Suburbs — North
    "60004", "60005", "60006", "60007", "60008", "60010", "60015", "60016",
    "60018", "60022", "60025", "60026", "60029", "60035", "60040", "60043",
    "60044", "60045", "60046", "60047", "60048", "60051", "60056", "60060",
    "60061", "60062", "60064", "60067", "60068", "60069", "60070", "60073",
    "60074", "60076", "60077", "60081", "60083", "60085", "60087", "60088",
    "60089", "60090", "60091", "60093", "60096", "60097", "60099",
    # Suburbs — West
    "60101", "60103", "60104", "60106", "60107", "60108", "60110", "60115",
    "60118", "60119", "60120", "60123", "60124", "60126", "60130", "60131",
    "60133", "60134", "60137", "60139", "60141", "60143", "60148", "60153",
    "60154", "60155", "60156", "60157", "60160", "60162", "60163", "60164",
    "60165", "60169", "60171", "60172", "60173", "60174", "60175", "60176",
    "60177", "60181", "60184", "60185", "60187", "60188", "60189", "60190",
    "60191", "60193", "60194", "60195", "60197", "60199",
    # Suburbs — South
    "60401", "60402", "60403", "60404", "60406", "60408", "60409", "60410",
    "60411", "60415", "60416", "60417", "60419", "60420", "60421", "60422",
    "60423", "60425", "60426", "60428", "60429", "60430", "60431", "60432",
    "60433", "60435", "60436", "60438", "60439", "60440", "60441", "60442",
    "60443", "60444", "60445", "60446", "60447", "60448", "60449", "60450",
    "60451", "60452", "60453", "60455", "60456", "60457", "60458", "60459",
    "60461", "60462", "60463", "60464", "60465", "60466", "60467", "60469",
    "60471", "60472", "60473", "60475", "60476", "60477", "60478", "60480",
    "60481", "60482", "60484", "60487", "60490", "60491", "60501", "60503",
    "60504", "60505", "60506", "60510", "60511", "60512", "60513", "60514",
    "60515", "60516", "60517", "60521", "60523", "60525", "60526", "60527",
    "60532", "60534", "60538", "60539", "60540", "60541", "60542", "60543",
    "60544", "60545", "60546", "60548", "60550", "60554", "60555", "60558",
    "60559", "60560", "60561", "60563", "60564", "60565", "60585", "60586",
    # Rockford area
    "61101", "61102", "61103", "61104", "61107", "61108", "61109",
    "61010", "61011", "61013", "61016", "61021", "61024", "61025",
    "61030", "61031", "61032", "61036", "61038", "61039", "61041",
    "61042", "61043", "61044", "61046", "61048", "61049", "61050",
    "61052", "61053", "61054", "61057", "61060", "61061", "61062",
    "61063", "61064", "61065", "61067", "61068", "61071", "61072",
    "61073", "61074", "61075", "61076", "61077", "61078", "61079",
    "61080", "61081", "61084", "61085", "61087", "61088", "61089",
    # Peoria area
    "61601", "61602", "61603", "61604", "61605", "61606", "61607",
    "61610", "61611", "61612", "61613", "61614", "61615", "61616",
    "61520", "61523", "61524", "61525", "61526", "61528", "61529",
    "61530", "61531", "61532", "61533", "61534", "61535", "61536",
    "61537", "61539", "61540", "61541", "61542", "61543", "61544",
    "61545", "61546", "61547", "61548", "61550", "61552", "61553",
    "61554", "61555", "61558", "61559", "61560", "61561", "61562",
    "61563", "61564", "61565", "61567", "61568", "61569",
    # Springfield area
    "62701", "62702", "62703", "62704", "62706", "62707", "62708",
    "62711", "62712",
    "62521", "62522", "62523", "62524", "62526",  # Decatur
    "62201", "62203", "62204", "62205", "62206", "62207",  # East St. Louis
    "62220", "62221", "62222", "62223", "62224", "62225",  # Belleville
    "62234", "62236", "62239", "62240", "62241", "62242",
    "62243", "62244", "62245", "62246", "62247", "62248",
    "62249", "62250", "62253", "62254", "62255", "62257",
    "62258", "62260", "62261", "62262", "62263", "62264",
    "62265", "62266", "62268", "62269", "62271", "62272",
    "62273", "62274", "62275", "62277", "62278", "62279",
    "62281", "62282", "62284", "62285", "62286", "62288",
    "62289", "62290", "62292", "62293", "62294", "62295",
    "62297", "62298",
    # Champaign-Urbana
    "61820", "61821", "61822", "61824", "61825", "61826",
    "61801", "61802", "61803",
    # Bloomington-Normal
    "61701", "61702", "61704", "61705", "61709",
    # Other downstate cities
    "61301", "61350",  # La Salle / Ottawa
    "61401", "61402",  # Galesburg
    "61501",           # Canton
    "61701",           # Bloomington
    "61831", "61832", "61833", "61834",  # Danville
    "61901", "61910", "61911", "61912",  # Mattoon / Charleston
    "62001", "62002",  # Alton
    "62010", "62012", "62013", "62014",
    "62018", "62019", "62021", "62022",
    "62024", "62025", "62026",
    "62301", "62305", "62306",  # Quincy
    "62401", "62410", "62411", "62413",  # Effingham / Salem area
    "62501", "62502", "62503",  # Taylorville area
    "62801", "62803", "62806",  # Centralia / Mt. Vernon area
    "62901", "62902", "62903",  # Carbondale
    "62960", "62966",           # Herrin / Marion
]

# Deduplicate while preserving order
IL_ZIPS = list(dict.fromkeys(IL_ZIPS))


def scrape_il() -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        logger.warning("IL retailers: playwright not installed")
        return []
    return _playwright_scrape()


def _playwright_scrape() -> list[dict]:
    from playwright.sync_api import sync_playwright

    api_info: dict = {
        "url": None,
        "method": None,
        "req_headers": None,
        "post_body": None,
        "first_zip": IL_ZIPS[0],
    }
    retailers: list[dict] = []
    seen_ids: set[str] = set()

    def handle_route(route, request):
        from urllib.parse import urlparse
        url = request.url
        if request.resource_type not in ("xhr", "fetch"):
            route.continue_()
            return
        hostname = urlparse(url).hostname or ""
        low = url.lower()
        # Match IL lottery's own API calls related to retailers/stores
        if "illinoislottery.com" not in hostname and "lottery.il.gov" not in hostname:
            route.continue_()
            return
        if not any(kw in low for kw in ("store", "retailer", "location", "locator", "dealer", "vendor", "where")):
            route.continue_()
            return
        try:
            resp = route.fetch()
            if resp.ok:
                before = len(retailers)
                try:
                    _parse_and_add(resp.json(), seen_ids, retailers)
                except Exception:
                    pass
                if api_info["url"] is None and len(retailers) > before:
                    api_info["url"] = url
                    api_info["method"] = request.method
                    api_info["req_headers"] = dict(request.headers)
                    api_info["post_body"] = request.post_data
                    logger.info("IL: discovered retailer API: %s %s", request.method, url)
        except Exception as e:
            logger.debug("IL: route fetch error: %s", e)
        route.continue_()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = ctx.new_page()
        page.route("**/*", handle_route)

        try:
            page.goto(LOCATOR_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            logger.warning("IL: locator page load error: %s", e)

        _search_zip(page, IL_ZIPS[0])
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        remaining_zips = IL_ZIPS[1:]

        if api_info["url"]:
            page.unroute_all()
            browser.close()
            logger.info("IL: API discovered, fetching %d remaining zips via HTTP", len(remaining_zips))
            _bulk_fetch(api_info, remaining_zips, seen_ids, retailers)
        else:
            logger.warning("IL: API not discovered, using Playwright for remaining zips")
            for zipcode in remaining_zips:
                _search_zip(page, zipcode)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                time.sleep(0.5)
            browser.close()

    logger.info("IL: scraped %d unique retailers", len(retailers))
    return retailers


def _search_zip(page, zipcode: str) -> None:
    try:
        inp = (
            page.query_selector("input[placeholder*='zip' i]") or
            page.query_selector("input[placeholder*='location' i]") or
            page.query_selector("input[placeholder*='city' i]") or
            page.query_selector("input[placeholder*='address' i]") or
            page.query_selector("input[type='search']") or
            page.query_selector("input[type='text']:visible")
        )
        if inp:
            inp.triple_click()
            inp.type(zipcode, delay=50)

        btn = (
            page.query_selector("button[type='submit']") or
            page.query_selector("button:has-text('Search')") or
            page.query_selector("button:has-text('Find')") or
            page.query_selector("input[type='submit']")
        )
        if btn:
            btn.click()
        elif inp:
            inp.press("Enter")
    except Exception as e:
        logger.debug("IL: search interaction error for %s: %s", zipcode, e)


def _bulk_fetch(api_info: dict, zips: list[str], seen_ids: set, retailers: list) -> None:
    import requests
    session = requests.Session()
    headers = {k: v for k, v in (api_info["req_headers"] or {}).items()
               if k.lower() not in ("host", "content-length")}
    first_zip = api_info["first_zip"]
    base_url = api_info["url"]
    method = (api_info["method"] or "GET").upper()
    body_template = api_info["post_body"] or ""

    for zipcode in zips:
        try:
            url = base_url.replace(first_zip, zipcode)
            body = body_template.replace(first_zip, zipcode) if body_template else None
            if method == "POST":
                resp = session.post(url, data=body, headers=headers, timeout=20)
            else:
                resp = session.get(url, headers=headers, timeout=20)
            if resp.ok:
                _parse_and_add(resp.json(), seen_ids, retailers)
        except Exception as e:
            logger.debug("IL: bulk fetch error for %s: %s", zipcode, e)
        time.sleep(0.2)


def _parse_and_add(data, seen_ids: set, retailers: list) -> None:
    if not data:
        return
    items = data
    if isinstance(data, dict):
        for key in ("retailers", "locations", "stores", "results", "data", "items", "features"):
            val = data.get(key)
            if isinstance(val, list):
                items = val
                break
        else:
            return
    for item in items:
        if not isinstance(item, dict):
            continue
        # GeoJSON feature format
        if item.get("type") == "Feature":
            props = item.get("properties") or {}
            geom = item.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if coords and len(coords) >= 2:
                props["_lng"] = coords[0]
                props["_lat"] = coords[1]
            item = props
        r = _parse_retailer(item)
        if r and r["external_id"] not in seen_ids:
            seen_ids.add(r["external_id"])
            retailers.append(r)


def _parse_retailer(item: dict) -> dict | None:
    name = (
        item.get("name") or item.get("storeName") or item.get("businessName") or
        item.get("retailerName") or item.get("dbaName") or item.get("title") or ""
    ).strip()
    if not name:
        return None

    external_id = str(
        item.get("id") or item.get("retailerId") or item.get("storeId") or
        item.get("locationId") or item.get("retailer_id") or ""
    ).strip()
    if not external_id:
        external_id = make_external_id(
            name,
            item.get("address") or item.get("address1") or item.get("street") or "",
            item.get("zip") or item.get("zipCode") or item.get("postalCode") or "",
        )

    lat = lng = None
    try:
        lat = float(item.get("_lat") or item.get("latitude") or item.get("lat") or 0) or None
        lng = float(item.get("_lng") or item.get("longitude") or item.get("lng") or 0) or None
    except (ValueError, TypeError):
        pass

    return {
        "external_id": external_id,
        "name": name,
        "address": (item.get("address") or item.get("address1") or item.get("street") or "").strip() or None,
        "city": (item.get("city") or item.get("town") or "").strip() or None,
        "zip_code": (item.get("zip") or item.get("zipCode") or item.get("postalCode") or "").strip() or None,
        "phone": (item.get("phone") or item.get("phoneNumber") or item.get("telephone") or "").strip() or None,
        "latitude": lat,
        "longitude": lng,
    }


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_il)
    return await upsert_retailers(conn, "IL", retailers)
