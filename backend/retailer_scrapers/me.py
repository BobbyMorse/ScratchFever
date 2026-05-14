"""
Maine State Lottery retailer scraper.
Uses Playwright to discover the retailer-locator API by navigating to
https://www.mainelottery.com/players_info/where_to_buy.html
and intercepting the XHR/fetch call triggered by zip-code search.
Then uses direct HTTP for remaining zip codes.
"""
from __future__ import annotations
import logging
import time
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

LOCATOR_URL = "https://www.mainelottery.com/players_info/where_to_buy.html"

# Strategic zip codes covering all Maine regions
ME_ZIPS = [
    # Southern Maine / York County
    "03901", "03902", "03903", "03904", "03906", "03907", "03908", "03909",
    "04001", "04005", "04009", "04011", "04020", "04027", "04030", "04038",
    "04039", "04041", "04042", "04043", "04046", "04047", "04048",
    # Portland metro / Cumberland County
    "04101", "04102", "04103", "04105", "04106", "04107", "04109",
    "04032", "04062", "04063", "04072", "04073", "04074", "04079",
    "04083", "04084", "04085", "04086", "04087", "04090", "04092", "04093",
    # Lewiston / Auburn / Androscoggin County
    "04210", "04212", "04216", "04217", "04220", "04222", "04224", "04225",
    "04226", "04227", "04228", "04230", "04234", "04236", "04238", "04240",
    "04250", "04252", "04253", "04254", "04256", "04257", "04258", "04260",
    "04263", "04265", "04268", "04270", "04271", "04274", "04276", "04280",
    "04281", "04282", "04284", "04285", "04286", "04288", "04289", "04290",
    "04292",
    # Augusta / Kennebec County
    "04330", "04332", "04333", "04336", "04338", "04341", "04342", "04344",
    "04345", "04347", "04348", "04349", "04350", "04351", "04352", "04354",
    "04355", "04357", "04358", "04359", "04360", "04363", "04364",
    # Waterville / Somerset area
    "04901", "04902", "04903", "04910", "04911", "04912", "04915", "04917",
    "04920", "04921", "04922", "04924", "04925", "04926", "04927", "04928",
    "04929", "04930", "04932", "04938", "04939", "04940", "04941", "04942",
    "04943", "04945", "04947", "04949", "04950", "04952", "04953", "04954",
    "04955", "04956", "04957", "04958", "04961", "04963", "04964", "04966",
    "04967", "04969", "04970", "04971", "04972", "04973", "04974", "04976",
    "04978", "04979", "04981", "04982", "04983", "04984", "04985", "04986",
    "04987", "04988", "04989",
    # Bangor / Penobscot County
    "04401", "04402", "04403", "04405", "04406", "04408", "04410", "04411",
    "04412", "04413", "04414", "04415", "04416", "04417", "04418", "04419",
    "04420", "04421", "04422", "04424", "04426", "04427", "04428", "04429",
    "04430", "04431", "04434", "04435", "04438", "04441", "04442", "04443",
    "04444", "04448", "04449", "04450", "04451", "04452", "04453", "04454",
    "04455", "04456", "04457", "04459", "04460", "04461", "04462", "04463",
    "04464", "04468", "04469", "04471", "04472", "04473", "04474", "04475",
    "04476", "04478", "04479", "04481", "04485", "04487", "04488", "04490",
    "04491", "04492", "04493", "04495", "04496",
    # Knox / Waldo Counties (Rockland, Camden, Belfast)
    "04841", "04843", "04847", "04848", "04849", "04850", "04852", "04853",
    "04854", "04855", "04856", "04858", "04859", "04860", "04861", "04863",
    "04864",
    # Hancock County (Ellsworth, Bar Harbor)
    "04605", "04606", "04607", "04609", "04611", "04614", "04616", "04619",
    "04621", "04622", "04623", "04624", "04625", "04626", "04627", "04628",
    "04630", "04634", "04640", "04642", "04644", "04645", "04646", "04648",
    "04649", "04650", "04652", "04653", "04654", "04655", "04657", "04658",
    "04660", "04664", "04666", "04667", "04668", "04669", "04671", "04672",
    "04673", "04674", "04675", "04676", "04677", "04679", "04680", "04681",
    "04683", "04684", "04685", "04686",
    # Aroostook County (Presque Isle, Caribou, Houlton)
    "04730", "04732", "04733", "04734", "04735", "04736", "04737", "04738",
    "04739", "04740", "04741", "04742", "04743", "04744", "04745", "04746",
    "04747", "04750", "04751", "04752", "04753", "04754", "04755", "04756",
    "04757", "04758", "04760", "04761", "04762", "04763", "04764", "04765",
    "04766", "04768", "04769", "04772", "04773", "04774", "04775", "04776",
    "04777", "04779", "04780", "04781", "04783", "04785", "04786", "04787",
]


def scrape_me() -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        logger.warning("ME retailers: playwright not installed")
        return []
    return _playwright_scrape()


def _playwright_scrape() -> list[dict]:
    from playwright.sync_api import sync_playwright

    api_info: dict = {
        "url": None,
        "method": None,
        "req_headers": None,
        "post_body": None,
        "first_zip": ME_ZIPS[0],
    }
    retailers: list[dict] = []
    seen_ids: set[str] = set()

    def handle_route(route, request):
        url = request.url
        low = url.lower()
        if any(kw in low for kw in ("retailer", "store", "location", "locator", "dealer", "where", "buy")):
            if api_info["url"] is None:
                api_info["url"] = url
                api_info["method"] = request.method
                api_info["req_headers"] = dict(request.headers)
                api_info["post_body"] = request.post_data
                logger.info("ME: discovered retailer API: %s %s", request.method, url)
            try:
                resp = route.fetch()
                if resp.ok:
                    _parse_and_add(resp.json(), seen_ids, retailers)
            except Exception as e:
                logger.debug("ME: route fetch error: %s", e)
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
            logger.warning("ME: locator page error: %s", e)

        _search_zip(page, ME_ZIPS[0])
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        remaining_zips = ME_ZIPS[1:]

        if api_info["url"]:
            page.remove_all_listeners("route")
            browser.close()
            logger.info("ME: API discovered, fetching %d remaining zips via HTTP", len(remaining_zips))
            _bulk_fetch(api_info, remaining_zips, seen_ids, retailers)
        else:
            logger.warning("ME: API not discovered, using Playwright for %d remaining zips", len(remaining_zips))
            for zipcode in remaining_zips:
                _search_zip(page, zipcode)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                time.sleep(0.5)
            browser.close()

    logger.info("ME: scraped %d unique retailers", len(retailers))
    return retailers


def _search_zip(page, zipcode: str) -> None:
    try:
        inp = (
            page.query_selector("input[name*='zip' i]") or
            page.query_selector("input[placeholder*='zip' i]") or
            page.query_selector("input[name*='town' i]") or
            page.query_selector("input[placeholder*='city' i]") or
            page.query_selector("input[type='text']:visible") or
            page.query_selector("input[type='search']")
        )
        if inp:
            inp.triple_click()
            inp.type(zipcode, delay=50)

        btn = (
            page.query_selector("button[type='submit']") or
            page.query_selector("button:has-text('Search')") or
            page.query_selector("input[type='submit']")
        )
        if btn:
            btn.click()
        elif inp:
            inp.press("Enter")
    except Exception as e:
        logger.debug("ME: search interaction error for %s: %s", zipcode, e)


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
            logger.debug("ME: bulk fetch error for %s: %s", zipcode, e)
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
    retailers = await asyncio.to_thread(scrape_me)
    return await upsert_retailers(conn, "ME", retailers)
