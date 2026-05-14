"""
New Hampshire Lottery retailer scraper.
Uses Playwright to discover the retailer-locator API endpoint by navigating to
https://www.nhlottery.com/find-retailer, filling in a zip code, and intercepting
the XHR/fetch call. Then makes direct HTTP requests for all remaining NH zip codes.
"""
from __future__ import annotations
import re
import json
import logging
import time
from .base import safe_get, make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

LOCATOR_URL = "https://www.nhlottery.com/find-retailer"

NH_ZIPS = [
    "03031", "03032", "03033", "03034", "03036", "03037", "03038",
    "03042", "03043", "03044", "03045", "03046", "03047", "03048", "03049",
    "03051", "03052", "03053", "03054", "03055", "03057",
    "03060", "03062", "03063", "03064",
    "03070", "03071", "03076", "03077", "03079",
    "03082", "03084", "03086", "03087",
    "03101", "03102", "03103", "03104", "03106", "03109", "03110",
    "03215", "03216", "03217", "03218", "03220", "03221", "03222", "03223",
    "03224", "03225", "03226", "03227", "03229", "03230", "03231",
    "03233", "03234", "03235", "03237", "03238",
    "03240", "03241", "03242", "03243", "03244", "03245", "03246", "03249",
    "03251", "03253", "03254", "03255", "03256", "03257", "03258", "03259",
    "03260", "03261", "03262", "03263", "03264", "03266", "03268", "03269",
    "03273", "03275", "03276", "03278", "03279",
    "03280", "03281", "03282", "03284", "03285", "03287",
    "03290", "03291", "03293",
    "03301", "03303", "03304", "03307",
    "03431", "03440", "03441", "03442", "03443", "03444", "03445", "03446",
    "03447", "03448", "03449", "03450", "03451", "03452",
    "03455", "03456", "03457", "03458",
    "03461", "03462", "03464", "03465", "03466", "03467", "03470",
    "03561", "03570", "03574", "03575", "03576", "03579",
    "03580", "03581", "03582", "03583", "03584", "03585", "03586", "03588",
    "03590", "03592", "03593", "03595", "03597", "03598",
    "03601", "03602", "03603", "03604", "03605", "03607", "03608", "03609",
    "03740", "03741", "03743", "03745", "03748",
    "03750", "03751", "03752", "03753", "03755",
    "03765", "03766", "03768",
    "03770", "03771", "03773", "03774", "03777", "03779",
    "03780", "03781", "03782", "03784", "03785",
    "03801", "03809", "03810", "03811", "03812", "03813", "03814", "03816",
    "03817", "03818", "03819", "03820",
    "03823", "03824", "03825", "03826", "03827",
    "03830", "03832", "03833", "03835", "03836", "03837", "03838", "03839",
    "03840", "03841", "03842", "03844", "03845", "03846", "03847", "03848", "03849",
    "03850", "03851", "03852", "03853", "03854", "03855", "03856", "03857", "03858",
    "03860", "03861", "03862", "03864", "03865", "03867", "03868", "03869",
    "03870", "03871", "03872", "03873", "03874", "03875", "03878",
    "03882", "03883", "03884", "03885", "03886", "03887", "03890", "03894", "03897",
]


def scrape_nh() -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        logger.warning("NH retailers: playwright not installed")
        return []
    return _playwright_scrape()


def _playwright_scrape() -> list[dict]:
    from playwright.sync_api import sync_playwright

    # Discovered API info filled in during first zip search
    api_info: dict = {
        "url": None,
        "method": None,
        "req_headers": None,
        "post_body": None,
        "first_zip": NH_ZIPS[0],
    }
    retailers: list[dict] = []
    seen_ids: set[str] = set()

    def handle_route(route, request):
        url = request.url
        low = url.lower()
        # Only intercept XHR/fetch calls, not document/page navigations
        if request.resource_type not in ("xhr", "fetch"):
            route.continue_()
            return
        # Only intercept first-party requests (nhlottery.com)
        if "nhlottery.com" not in url:
            route.continue_()
            return
        # Intercept any request that looks like a retailer search endpoint
        if any(kw in low for kw in ("retailer", "store", "location", "locator", "dealer", "vendor", "where")):
            if api_info["url"] is None:
                api_info["url"] = url
                api_info["method"] = request.method
                api_info["req_headers"] = dict(request.headers)
                api_info["post_body"] = request.post_data
                logger.info("NH: discovered retailer API: %s %s", request.method, url)
            # Fetch and capture results for this request
            try:
                resp = route.fetch()
                if resp.ok:
                    _parse_and_add(resp.json(), seen_ids, retailers)
            except Exception as e:
                logger.debug("NH: route fetch error: %s", e)
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
            logger.warning("NH: locator page error: %s", e)

        # Fill in the first zip code and trigger the search
        _search_zip(page, NH_ZIPS[0])
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        remaining_zips = NH_ZIPS[1:]

        if api_info["url"]:
            # Discovered the API — use direct HTTP for speed
            page.unroute_all()  # stop intercepting
            browser.close()
            logger.info("NH: API discovered, fetching %d remaining zips via HTTP", len(remaining_zips))
            _bulk_fetch(api_info, remaining_zips, seen_ids, retailers)
        else:
            # No API found — use Playwright for all remaining zips
            logger.warning("NH: API not discovered, using Playwright for %d remaining zips", len(remaining_zips))
            for zipcode in remaining_zips:
                _search_zip(page, zipcode)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                time.sleep(0.5)
            browser.close()

    logger.info("NH: scraped %d unique retailers", len(retailers))
    return retailers


def _search_zip(page, zipcode: str) -> None:
    """Find the zip/location input, clear it, type the zip, and submit."""
    try:
        # Try common selectors for the search input
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
        logger.debug("NH: search interaction error for %s: %s", zipcode, e)


def _bulk_fetch(api_info: dict, zips: list[str], seen_ids: set, retailers: list) -> None:
    """Make direct HTTP requests to the discovered API for each zip code."""
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
            logger.debug("NH: bulk fetch error for %s: %s", zipcode, e)
        time.sleep(0.2)


def _parse_and_add(data, seen_ids: set, retailers: list) -> None:
    """Extract retailers from an API response and add new ones to the list."""
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
    retailers = await asyncio.to_thread(scrape_nh)
    return await upsert_retailers(conn, "NH", retailers)
