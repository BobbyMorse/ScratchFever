"""
Capture the exact headers sent by Playwright to the AWC API, then test reusing them.
"""
import json
import time
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://ris.p1.awc.lotteryservices.net"
SEED_URL = "https://www.rilot.com/en-us/player-zone/find-a-retailer.html"
LOCATIONS_PATH = "/api/v1/locations"


def main():
    captured_headers = {}

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

        full_responses = []

        def handle_route(route, request):
            if LOCATIONS_PATH in request.url:
                captured_headers.update(request.headers)
                print(f"\nIntercepted: {request.url}")
                print(f"Headers: {json.dumps(dict(request.headers), indent=2)}")
                try:
                    resp = route.fetch()
                    if resp.ok:
                        full_responses.append({
                            "url": request.url,
                            "data": resp.json()
                        })
                        print(f"Response: {len(resp.json().get('locations', []))} locations, nextItems={resp.json().get('nextItems')}")
                except Exception as e:
                    print(f"Route fetch error: {e}")
                route.continue_()
            else:
                route.continue_()

        page.route("**/*", handle_route)

        page.goto(SEED_URL, wait_until="networkidle", timeout=30_000)
        time.sleep(2)
        browser.close()

    if not captured_headers:
        print("No headers captured")
        return

    # Try using captured headers directly
    print("\n\n--- Testing direct request with captured headers ---")
    session = requests.Session()
    # Filter out hop-by-hop headers
    skip = {"content-length", "transfer-encoding", "connection", "host"}
    for k, v in captured_headers.items():
        if k.lower() not in skip:
            session.headers[k] = v

    r = session.get(f"{BASE_URL}/api/v1/locations", params={"city": "Providence"}, timeout=15)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"Got {len(data.get('locations', []))} locations, nextItems={data.get('nextItems')}")
        print(f"nextPageUrl: {data.get('nextPageUrl')}")
    else:
        print(f"Response: {r.text[:300]}")

    # Also test large size
    if r.status_code == 200:
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(data.get("nextPageUrl", "")).query)
        seed = params.get("seed", [None])[0]
        if seed:
            r2 = session.get(
                f"{BASE_URL}/api/v1/locations/page",
                params={"city": "Providence", "page": 1, "size": 500, "seed": seed},
                timeout=15,
            )
            print(f"\nSize=500: status={r2.status_code}")
            if r2.status_code == 200:
                d2 = r2.json()
                print(f"Got {len(d2.get('locations', []))} locations, nextItems={d2.get('nextItems')}")


if __name__ == "__main__":
    main()
