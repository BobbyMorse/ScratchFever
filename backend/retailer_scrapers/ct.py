"""
Connecticut Lottery retailer scraper.
Uses Playwright to discover the retailer-locator API by navigating to
https://www.ctlottery.org/WhereToPlay/107262
and intercepting the XHR/fetch call triggered by location search.
Then uses direct HTTP for remaining CT zip codes.
"""
from __future__ import annotations
import logging
import time
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

LOCATOR_URL = "https://www.ctlottery.org/WhereToPlay/107262"

# Strategic zip codes covering all Connecticut regions
CT_ZIPS = [
    # Hartford County
    "06101", "06103", "06105", "06106", "06107", "06108", "06109", "06110",
    "06111", "06112", "06114", "06118", "06119", "06120",
    "06010", "06013", "06016", "06018", "06023", "06026", "06027", "06028",
    "06029", "06032", "06033", "06035", "06037", "06040", "06041", "06042",
    "06043", "06052", "06057", "06058", "06059", "06060", "06061", "06062",
    "06063", "06065", "06066", "06067", "06070", "06071", "06073", "06074",
    "06075", "06076", "06077", "06078", "06081", "06082", "06083", "06084",
    "06085", "06088", "06089", "06090", "06091", "06092", "06093", "06094",
    "06095", "06096", "06098",
    # New Haven County
    "06401", "06403", "06405", "06408", "06410", "06412", "06413", "06414",
    "06415", "06416", "06417", "06418", "06419", "06420", "06422", "06423",
    "06424", "06426", "06437", "06438", "06439", "06441", "06442", "06443",
    "06444", "06447", "06450", "06451", "06455", "06456", "06457", "06459",
    "06460", "06461", "06467", "06468", "06469", "06471", "06472", "06473",
    "06477", "06478", "06480", "06481", "06482", "06483", "06484", "06488",
    "06489", "06491",
    "06501", "06510", "06511", "06512", "06513", "06514", "06515", "06516",
    "06517", "06518", "06519",
    # Fairfield County
    "06601", "06602", "06604", "06605", "06606", "06607", "06608", "06610",
    "06611", "06612",
    "06615", "06820", "06824", "06825", "06828", "06829",
    "06830", "06831", "06832", "06840", "06850", "06851", "06853", "06854",
    "06855", "06856", "06870", "06877", "06878", "06880", "06883", "06884",
    "06890", "06896", "06897",
    "06801", "06804", "06807", "06810", "06811", "06812", "06814", "06816",
    "06817", "06820",
    # Middlesex County
    "06409", "06416", "06417", "06419", "06422", "06423", "06424", "06426",
    "06455", "06456", "06457", "06459", "06469", "06480",
    # New London County
    "06320", "06330", "06331", "06332", "06333", "06334", "06335", "06336",
    "06338", "06339", "06340", "06349", "06351", "06354", "06355", "06357",
    "06359", "06360", "06365", "06370", "06371", "06372", "06373", "06374",
    "06375", "06376", "06377", "06378", "06379", "06380", "06382", "06383",
    "06384", "06385", "06386", "06387", "06388", "06389",
    # Tolland County
    "06029", "06066", "06071", "06076", "06084", "06226", "06231", "06232",
    "06233", "06234", "06235", "06237", "06238", "06239", "06241", "06242",
    "06243", "06244", "06245", "06246", "06247", "06248", "06249", "06250",
    "06251", "06254", "06255", "06256", "06258", "06259", "06260", "06262",
    "06263", "06264", "06265", "06266", "06268", "06269", "06277", "06278",
    "06279", "06280", "06281", "06282",
    # Windham County
    "06226", "06231", "06234", "06239", "06241", "06242", "06243", "06244",
    "06245", "06246", "06247", "06248", "06249", "06250", "06251", "06254",
    "06255", "06256", "06258", "06259", "06260", "06262", "06263", "06264",
    "06265", "06266", "06268", "06277", "06278", "06279", "06280", "06281",
    "06282",
    # Litchfield County
    "06018", "06020", "06021", "06022", "06024", "06031", "06039", "06057",
    "06058", "06059", "06060", "06061", "06062", "06063", "06065", "06068",
    "06069", "06750", "06751", "06752", "06753", "06754", "06755", "06756",
    "06757", "06758", "06759", "06762", "06763", "06776", "06777", "06778",
    "06779", "06781", "06782", "06783", "06784", "06785", "06786", "06787",
    "06788", "06790", "06791", "06793", "06794", "06795", "06796", "06798",
]

# Deduplicate while preserving order
_seen: set[str] = set()
CT_ZIPS = [z for z in CT_ZIPS if not (_seen.add(z) or z in _seen - {z})]
# Simpler dedup:
CT_ZIPS = list(dict.fromkeys(CT_ZIPS))


def scrape_ct() -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        logger.warning("CT retailers: playwright not installed")
        return []
    return _playwright_scrape()


def _playwright_scrape() -> list[dict]:
    from playwright.sync_api import sync_playwright

    api_info: dict = {
        "url": None,
        "method": None,
        "req_headers": None,
        "post_body": None,
        "first_zip": CT_ZIPS[0],
    }
    retailers: list[dict] = []
    seen_ids: set[str] = set()

    def handle_route(route, request):
        url = request.url
        low = url.lower()
        if any(kw in low for kw in ("retailer", "store", "location", "locator", "wheretoplay", "dealer", "vendor")):
            if api_info["url"] is None:
                api_info["url"] = url
                api_info["method"] = request.method
                api_info["req_headers"] = dict(request.headers)
                api_info["post_body"] = request.post_data
                logger.info("CT: discovered retailer API: %s %s", request.method, url)
            try:
                resp = route.fetch()
                if resp.ok:
                    _parse_and_add(resp.json(), seen_ids, retailers)
            except Exception as e:
                logger.debug("CT: route fetch error: %s", e)
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
            page.goto(LOCATOR_URL, wait_until="networkidle", timeout=30_000)
        except Exception as e:
            logger.warning("CT: locator page error: %s", e)

        _search_zip(page, CT_ZIPS[0])
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        remaining_zips = CT_ZIPS[1:]

        if api_info["url"]:
            page.remove_all_listeners("route")
            browser.close()
            logger.info("CT: API discovered, fetching %d remaining zips via HTTP", len(remaining_zips))
            _bulk_fetch(api_info, remaining_zips, seen_ids, retailers)
        else:
            logger.warning("CT: API not discovered, using Playwright for %d remaining zips", len(remaining_zips))
            for zipcode in remaining_zips:
                _search_zip(page, zipcode)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                time.sleep(0.5)
            browser.close()

    logger.info("CT: scraped %d unique retailers", len(retailers))
    return retailers


def _search_zip(page, zipcode: str) -> None:
    try:
        inp = (
            page.query_selector("input[name*='location' i]") or
            page.query_selector("input[placeholder*='location' i]") or
            page.query_selector("input[placeholder*='zip' i]") or
            page.query_selector("input[placeholder*='city' i]") or
            page.query_selector("input[type='text']:visible") or
            page.query_selector("input[type='search']")
        )
        if inp:
            inp.triple_click()
            inp.type(zipcode, delay=50)

        btn = (
            page.query_selector("button[type='submit']") or
            page.query_selector("a:has-text('Search')") or
            page.query_selector("button:has-text('Search')") or
            page.query_selector("input[type='submit']")
        )
        if btn:
            btn.click()
        elif inp:
            inp.press("Enter")
    except Exception as e:
        logger.debug("CT: search interaction error for %s: %s", zipcode, e)


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
            logger.debug("CT: bulk fetch error for %s: %s", zipcode, e)
        time.sleep(0.2)


def _parse_and_add(data, seen_ids: set, retailers: list) -> None:
    if not data:
        return
    items = data
    if isinstance(data, dict):
        for key in ("retailers", "locations", "stores", "results", "data", "items"):
            val = data.get(key)
            if isinstance(val, list):
                items = val
                break
        else:
            return
    for item in items:
        if not isinstance(item, dict):
            continue
        r = _parse_retailer(item)
        if r and r["external_id"] not in seen_ids:
            seen_ids.add(r["external_id"])
            retailers.append(r)


def _parse_retailer(item: dict) -> dict | None:
    name = (
        item.get("name") or item.get("storeName") or item.get("businessName") or
        item.get("retailerName") or item.get("dbaName") or ""
    ).strip()
    if not name:
        return None

    external_id = str(
        item.get("id") or item.get("retailerId") or item.get("storeId") or
        item.get("locationId") or ""
    ).strip()
    if not external_id:
        external_id = make_external_id(
            name,
            item.get("address") or item.get("address1") or "",
            item.get("zip") or item.get("zipCode") or item.get("postalCode") or "",
        )

    lat = lng = None
    try:
        lat = float(item["latitude"]) if item.get("latitude") is not None else None
        lng = float(item["longitude"]) if item.get("longitude") is not None else None
    except (ValueError, TypeError):
        pass

    return {
        "external_id": external_id,
        "name": name,
        "address": (item.get("address") or item.get("address1") or item.get("street") or "").strip() or None,
        "city": (item.get("city") or item.get("town") or "").strip() or None,
        "zip_code": (item.get("zip") or item.get("zipCode") or item.get("postalCode") or "").strip() or None,
        "phone": (item.get("phone") or item.get("phoneNumber") or "").strip() or None,
        "latitude": lat,
        "longitude": lng,
    }


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_ct)
    return await upsert_retailers(conn, "CT", retailers)
