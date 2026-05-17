"""
Vermont Lottery retailer scraper.
Uses Playwright to discover the retailer-locator API by navigating to
https://www.vtlottery.com/where-to-play and intercepting the XHR/fetch call
triggered by a zip-code search. Then uses direct HTTP for remaining VT zip codes.
"""
from __future__ import annotations
import logging
import time
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

LOCATOR_URL = "https://www.vtlottery.com/where-to-play"

VT_ZIPS = [
    # Chittenden County (Burlington metro)
    "05401", "05402", "05403", "05404", "05405", "05406", "05407", "05408",
    "05439", "05446", "05449", "05451", "05452", "05453", "05454", "05461",
    "05462", "05465", "05468", "05477", "05482", "05489", "05490", "05494", "05495",
    # Franklin / Grand Isle counties
    "05440", "05441", "05448", "05450", "05455", "05457", "05459", "05460",
    "05463", "05474", "05478", "05479", "05481", "05483", "05485", "05486", "05488",
    # Washington County (Montpelier)
    "05601", "05602", "05603", "05604", "05641", "05647", "05648", "05649",
    "05650", "05651", "05656", "05658", "05660", "05663", "05664", "05666",
    "05667", "05669", "05670", "05671", "05676", "05677", "05678", "05679",
    "05680", "05681", "05682",
    # Lamoille County
    "05442", "05444", "05452", "05464", "05655", "05657", "05661", "05662",
    "05672", "05492",
    # Orange County
    "05033", "05036", "05038", "05039", "05040", "05041", "05042", "05043",
    "05045", "05046", "05052", "05054", "05060", "05061", "05065", "05070",
    "05072", "05073", "05074", "05075", "05076", "05081", "05083", "05084",
    "05086",
    # Windsor County
    "05001", "05009", "05030", "05031", "05032", "05034", "05035", "05037",
    "05048", "05049", "05055", "05056", "05057", "05058", "05059", "05062",
    "05067", "05068", "05069", "05071", "05079", "05085", "05088", "05089", "05091",
    # Windham County (Brattleboro)
    "05301", "05302", "05303", "05304", "05340", "05341", "05342", "05343",
    "05344", "05345", "05346", "05350", "05351", "05353", "05354", "05355",
    "05356", "05357", "05358", "05359", "05360", "05361", "05362", "05363",
    # Bennington County
    "05201", "05250", "05251", "05252", "05253", "05254", "05255", "05257",
    "05260", "05261", "05262",
    # Rutland County
    "05701", "05702", "05730", "05731", "05732", "05733", "05734", "05735",
    "05736", "05737", "05738", "05739", "05741", "05742", "05743", "05745",
    "05748", "05749", "05753", "05754", "05755", "05757", "05759", "05760",
    "05763", "05764", "05765", "05766", "05767", "05769", "05770", "05772",
    "05773", "05774", "05775", "05776", "05777", "05778",
    # Addison County
    "05443", "05445", "05456", "05469", "05472", "05473", "05491",
    # Caledonia County (St. Johnsbury)
    "05819", "05821", "05824", "05828", "05832", "05836", "05838",
    "05841", "05843", "05848", "05849", "05850", "05851", "05861", "05862",
    "05863", "05865", "05871", "05873", "05875",
    # Orleans County
    "05822", "05825", "05826", "05827", "05829", "05830", "05833", "05839",
    "05845", "05847", "05853", "05855", "05857", "05859", "05860", "05866",
    "05867", "05868", "05869", "05872", "05874",
    # Essex County (NE Kingdom)
    "05820", "05823", "05837", "05840", "05846", "05858", "05901", "05902",
    "05903", "05904", "05905", "05906", "05907",
]


def scrape_vt() -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        logger.warning("VT retailers: playwright not installed")
        return []
    return _playwright_scrape()


def _playwright_scrape() -> list[dict]:
    from playwright.sync_api import sync_playwright

    api_info: dict = {
        "url": None,
        "method": None,
        "req_headers": None,
        "post_body": None,
        "first_zip": VT_ZIPS[0],
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
        if "vtlottery.com" not in hostname and "lottery" not in hostname:
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
                    logger.info("VT: discovered retailer API: %s %s", request.method, url)
        except Exception as e:
            logger.debug("VT: route fetch error: %s", e)
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
            logger.warning("VT: locator page error: %s", e)

        _search_zip(page, VT_ZIPS[0])
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        remaining_zips = VT_ZIPS[1:]

        if api_info["url"]:
            page.unroute_all()
            browser.close()
            logger.info("VT: API discovered, fetching %d remaining zips via HTTP", len(remaining_zips))
            _bulk_fetch(api_info, remaining_zips, seen_ids, retailers)
        else:
            logger.warning("VT: API not discovered, using Playwright for %d remaining zips", len(remaining_zips))
            for zipcode in remaining_zips:
                _search_zip(page, zipcode)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                time.sleep(0.5)
            browser.close()

    logger.info("VT: scraped %d unique retailers", len(retailers))
    return retailers


def _search_zip(page, zipcode: str) -> None:
    try:
        inp = (
            page.query_selector("input[name*='zip' i]") or
            page.query_selector("input[placeholder*='zip' i]") or
            page.query_selector("input[placeholder*='city' i]") or
            page.query_selector("input[placeholder*='location' i]") or
            page.query_selector("input[type='text']:visible") or
            page.query_selector("input[type='search']")
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
        logger.debug("VT: search interaction error for %s: %s", zipcode, e)


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
            logger.debug("VT: bulk fetch error for %s: %s", zipcode, e)
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
        item.get("locationId") or item.get("retailer_id") or ""
    ).strip()
    if not external_id:
        external_id = make_external_id(
            name,
            item.get("address") or item.get("address1") or "",
            item.get("zip") or item.get("zipCode") or item.get("postalCode") or "",
        )

    address = (item.get("address") or item.get("address1") or item.get("street") or "").strip() or None
    city = (item.get("city") or item.get("town") or "").strip() or None
    zip_code = (item.get("zip") or item.get("zipCode") or item.get("postalCode") or "").strip() or None
    phone = (item.get("phone") or item.get("phoneNumber") or item.get("telephone") or "").strip() or None

    lat = lng = None
    try:
        lat = float(item["latitude"]) if item.get("latitude") is not None else None
        lng = float(item["longitude"]) if item.get("longitude") is not None else None
    except (ValueError, TypeError):
        pass

    return {
        "external_id": external_id,
        "name": name,
        "address": address,
        "city": city,
        "zip_code": zip_code,
        "phone": phone,
        "latitude": lat,
        "longitude": lng,
    }


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_vt)
    return await upsert_retailers(conn, "VT", retailers)
