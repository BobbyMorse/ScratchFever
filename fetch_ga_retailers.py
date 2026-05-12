"""
Fetch all Georgia Lottery retailers and save to ga_retailers.csv.

Strategy:
  1. Boot Playwright once to load the retailer page and do a sample zip search —
     intercepts the actual retailer API call to capture the endpoint URL and any
     auth headers (x-esa-api-key or similar).
  2. Use plain requests with those headers to iterate every GA zip code in the
     range 30002–39901, paginating through all results, then deduplicate by ID.

Usage:
    python fetch_ga_retailers.py

Output:
    ga_retailers.csv
"""

import csv
import time
import json
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH   = Path(__file__).parent / "ga_retailers.csv"
SEED_URL   = "https://www.galottery.com/en-us/player-zone/where-to-play.html"
SEED_ZIP   = "30301"

FIELDNAMES = ["id", "name", "address", "city", "state", "zipCode", "phone", "latitude", "longitude"]

# Georgia zip codes span 30002–31999 and 39813–39901.
# We iterate the full user-specified range 30002–39901 and skip empties.
GA_ZIP_RANGE = range(30002, 39902)


# ---------------------------------------------------------------------------
# Phase 1 — Playwright endpoint discovery
# ---------------------------------------------------------------------------

def discover_api() -> dict:
    """
    Load the GA retailer page, submit a sample zip, and intercept the retailer
    API call.  Returns a dict with keys: base_url, path, headers, params.
    """
    from playwright.sync_api import sync_playwright

    captured = {}

    SKIP = [
        "google", "bing", "facebook", "doubleclick", "analytics",
        "visualwebsite", "recaptcha", "pagead", "bat.bing",
        ".css", ".js", ".woff", ".png", ".jpg", ".gif", ".ico", ".svg",
    ]

    def looks_like_retailer(url: str) -> bool:
        u = url.lower()
        if any(s in u for s in SKIP):
            return False
        return any(k in u for k in ["retailer", "location", "store", "locator", "where", "awc"])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        def on_request(req):
            if looks_like_retailer(req.url) and not captured:
                captured["url"]     = req.url
                captured["method"]  = req.method
                captured["headers"] = dict(req.headers)
                captured["post_data"] = req.post_data
                logger.info("Captured retailer API call: %s", req.url)

        page.on("request", on_request)

        logger.info("Loading GA lottery retailer page …")
        try:
            page.goto(SEED_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            logger.warning("Page load warning: %s", e)
        time.sleep(2)

        # Fill the zip code input and submit
        try:
            inp = page.locator('input[type="text"]').first
            inp.fill(SEED_ZIP)
            logger.info("Filled zip input with %s", SEED_ZIP)
        except Exception as e:
            logger.warning("Could not fill zip input: %s", e)

        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

        # Also try clicking a search/submit button
        for sel in ['button:has-text("Search")', 'input[type="submit"]', 'button[type="submit"]']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    break
            except Exception:
                pass

        time.sleep(4)
        browser.close()

    if not captured:
        raise RuntimeError(
            "Could not intercept a retailer API call from the GA lottery page.\n"
            "The site may use a different mechanism — inspect the network tab manually\n"
            "and update API_BASE / API_PATH / SEARCH_PARAMS below."
        )

    logger.info("Discovered endpoint: %s", captured["url"])
    return captured


# ---------------------------------------------------------------------------
# Phase 2 — Iterate zip codes
# ---------------------------------------------------------------------------

def build_session(headers: dict) -> requests.Session:
    s = requests.Session()
    # Keep only the headers that are likely needed for auth / CORS
    keep = {
        k: v for k, v in headers.items()
        if any(x in k.lower() for x in [
            "accept", "origin", "referer", "user-agent",
            "x-esa-api-key", "x-user-agent", "authorization", "api-key",
        ])
    }
    s.headers.update(keep)
    return s


def fetch_zip(session: requests.Session, base_url: str, zip_code: str) -> list[dict]:
    """Fetch all retailers for a given zip, following pagination if needed."""
    retailers = []

    # Try common param names the AWC/GA API might use
    for zip_param in ("zip", "postalCode", "zipCode", "location"):
        try:
            r = session.get(base_url, params={zip_param: zip_code}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                locs = (
                    data.get("locations")
                    or data.get("retailers")
                    or data.get("results")
                    or (data if isinstance(data, list) else [])
                )
                if locs:
                    retailers.extend(locs)
                    # Pagination
                    next_items = data.get("nextItems", 0) if isinstance(data, dict) else 0
                    next_url   = data.get("nextPageUrl", "") if isinstance(data, dict) else ""
                    if next_items and next_url:
                        pqs  = parse_qs(urlparse(next_url).query)
                        seed = pqs.get("seed", [None])[0]
                        page = 1
                        while seed:
                            pr = session.get(
                                base_url.replace("/locations", "/locations/page"),
                                params={zip_param: zip_code, "page": page, "size": 10, "seed": seed},
                                timeout=15,
                            )
                            if pr.status_code != 200:
                                break
                            pd = pr.json()
                            more = pd.get("locations", [])
                            retailers.extend(more)
                            if not more or pd.get("nextItems", 0) == 0:
                                break
                            page += 1
                            time.sleep(0.1)
                    break  # found the right param
        except Exception as e:
            logger.debug("Error fetching zip %s with param %s: %s", zip_code, zip_param, e)

    return retailers


def normalize(raw: dict) -> dict:
    return {
        "id":        str(raw.get("id") or raw.get("retailerId") or ""),
        "name":      (raw.get("name") or raw.get("retailerName") or "").strip().title(),
        "address":   (raw.get("address1") or raw.get("streetAddress") or raw.get("address") or "").strip().title(),
        "city":      (raw.get("city") or "").strip().title(),
        "state":     "GA",
        "zipCode":   str(raw.get("postalCode") or raw.get("zipCode") or raw.get("zip") or "").strip(),
        "phone":     str(raw.get("phoneNumber") or raw.get("phone") or "").strip(),
        "latitude":  str(raw.get("lattitude") or raw.get("latitude") or raw.get("lat") or ""),
        "longitude": str(raw.get("longitude") or raw.get("lng") or raw.get("long") or ""),
    }


def main():
    # Phase 1: discover the API
    logger.info("=== Phase 1: API discovery via Playwright ===")
    captured = discover_api()

    # Parse the base URL (strip query string)
    parsed   = urlparse(captured["url"])
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    logger.info("Base URL for requests: %s", base_url)

    session  = build_session(captured.get("headers", {}))

    # Log captured info for debugging
    with open("ga_api_discovery.json", "w") as f:
        json.dump(captured, f, indent=2)
    logger.info("Full discovery dump → ga_api_discovery.json")

    # Phase 2: iterate zip codes
    logger.info("=== Phase 2: Iterating GA zip codes 30002–39901 ===")
    zips = [f"{z:05d}" for z in GA_ZIP_RANGE]
    logger.info("Total zip codes to query: %d", len(zips))

    seen: dict[str, dict] = {}
    empty_count = 0

    for i, zip_code in enumerate(zips):
        records = fetch_zip(session, base_url, zip_code)

        new = 0
        for raw in records:
            rid = raw.get("id") or raw.get("retailerId")
            if rid and str(rid) not in seen:
                seen[str(rid)] = normalize(raw)
                new += 1

        if not records:
            empty_count += 1

        if new > 0 or (i + 1) % 500 == 0:
            logger.info(
                "[%d/%d] zip=%s  found=%d  new=%d  total=%d",
                i + 1, len(zips), zip_code, len(records), new, len(seen),
            )

        time.sleep(0.15)

    logger.info(
        "Done. Unique retailers: %d  |  Empty zips: %d/%d",
        len(seen), empty_count, len(zips),
    )

    records_out = [r for r in seen.values() if r["name"]]
    records_out.sort(key=lambda r: (r["city"], r["name"]))

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records_out)

    logger.info("Saved %d retailers → %s", len(records_out), OUT_PATH)


if __name__ == "__main__":
    main()
