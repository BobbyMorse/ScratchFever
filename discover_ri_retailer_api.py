"""
Discover RI retailer API: intercept the locations API call via Playwright.
Tries several ZIP codes and captures response shape + cookies.
"""
import json
import time
from playwright.sync_api import sync_playwright

RETAILER_URL = "https://www.rilot.com/en-us/player-zone/find-a-retailer.html"
LOCATIONS_API = "/api/v1/locations"

ZIPS_TO_TRY = ["02831", "02903", "02860", "02840", "02895"]


def scrape_zip(page, zip_code):
    captured = {"data": None, "request_url": None}

    def handle_route(route, request):
        if LOCATIONS_API in request.url:
            captured["request_url"] = request.url
            try:
                resp = route.fetch()
                if resp.ok:
                    captured["data"] = resp.json()
            except Exception as e:
                print(f"  Route fetch error: {e}")
            route.continue_()
        else:
            route.continue_()

    page.route("**/*", handle_route)

    try:
        page.goto(f"{RETAILER_URL}?zipCity={zip_code}", wait_until="networkidle", timeout=30_000)
    except Exception as e:
        print(f"  Nav error: {e}")

    if captured["data"] is None:
        time.sleep(3)

    page.unroute("**/*")
    return captured


def main():
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

        for zip_code in ZIPS_TO_TRY:
            print(f"\n--- ZIP {zip_code} ---")
            result = scrape_zip(page, zip_code)
            if result["request_url"]:
                print(f"  API URL: {result['request_url']}")
            if result["data"] is not None:
                data = result["data"]
                dtype = type(data).__name__
                if isinstance(data, list):
                    print(f"  Got list of {len(data)} items")
                    if data:
                        print(f"  First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else 'not dict'}")
                        print(f"  First item: {json.dumps(data[0], indent=2)[:500]}")
                elif isinstance(data, dict):
                    print(f"  Dict keys: {list(data.keys())}")
                    print(f"  Data: {json.dumps(data, indent=2)[:500]}")
                else:
                    print(f"  Type: {dtype}")
            else:
                print("  No data captured")

            # Don't hammer — one zip is enough if we get data
            if result["data"] is not None:
                # Save full response
                with open(f"ri_locations_{zip_code}.json", "w") as f:
                    json.dump(result["data"], f, indent=2)
                print(f"  Saved to ri_locations_{zip_code}.json")
                break

        browser.close()


if __name__ == "__main__":
    main()
