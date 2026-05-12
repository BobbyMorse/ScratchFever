"""
Discover retailer API endpoints by intercepting network requests via Playwright.
Visits each state's retailer locator page, does a sample zip search, and captures API calls.
"""
import json
import time
from playwright.sync_api import sync_playwright

STATES = [
    {"code": "FL", "url": "https://floridalottery.com/where-to-play",          "zip": "33101", "zip_selector": None},
    {"code": "NY", "url": "https://nylottery.ny.gov/find-a-retailer",           "zip": "10001", "zip_selector": None},
    {"code": "NJ", "url": "https://www.njlottery.com/content/portal/en/retailer/findretailer.html", "zip": "07001", "zip_selector": None},
    {"code": "GA", "url": "https://www.galottery.com/en-us/player-zone/where-to-play.html", "zip": "30301", "zip_selector": None},
    {"code": "NC", "url": "https://nclottery.com/wheretoplay",                  "zip": "27601", "zip_selector": None},
    {"code": "TX", "url": "https://www.texaslottery.com/opencms/Games/Scratch_Offs/Retailer_Locator.jsp", "zip": "78201", "zip_selector": None},
    {"code": "CA", "url": "https://www.calottery.com/en/where-to-play",         "zip": "90001", "zip_selector": None},
    {"code": "VA", "url": "https://www.valottery.com",                           "zip": "23219", "zip_selector": None},
    {"code": "MD", "url": "https://www.mdlottery.com",                           "zip": "21201", "zip_selector": None},
    {"code": "CT", "url": "https://www.ctlottery.org",                           "zip": "06101", "zip_selector": None},
    {"code": "RI", "url": "https://www.rilot.com",                               "zip": "02901", "zip_selector": None},
    {"code": "SC", "url": "https://www.sceducationlottery.com",                  "zip": "29201", "zip_selector": None},
    {"code": "PA", "url": "https://www.palottery.pa.gov",                        "zip": "17101", "zip_selector": None},
    {"code": "MI", "url": "https://www.michiganlottery.com",                     "zip": "48201", "zip_selector": None},
    {"code": "MN", "url": "https://www.mnlottery.com",                           "zip": "55101", "zip_selector": None},
    {"code": "WI", "url": "https://www.wilottery.com",                           "zip": "53701", "zip_selector": None},
    {"code": "CO", "url": "https://www.coloradolottery.com",                     "zip": "80201", "zip_selector": None},
    {"code": "ID", "url": "https://www.idaholottery.com",                        "zip": "83701", "zip_selector": None},
    {"code": "NM", "url": "https://www.nmlottery.com",                           "zip": "87101", "zip_selector": None},
    {"code": "AR", "url": "https://myarkansaslottery.com",                       "zip": "72201", "zip_selector": None},
    {"code": "MS", "url": "https://www.mslottery.com",                           "zip": "39201", "zip_selector": None},
]

SKIP_DOMAINS = [
    "google-analytics", "googletagmanager", "facebook", "twitter",
    "doubleclick", "analytics", "fonts.googleapis", "fonts.gstatic",
    "hotjar", "segment", "cdn.jsdelivr", "cloudflare",
    ".css", ".woff", ".woff2", ".ttf", ".svg", ".png", ".jpg", ".gif", ".ico",
]

def is_api_call(url: str) -> bool:
    url_lower = url.lower()
    if any(skip in url_lower for skip in SKIP_DOMAINS):
        return False
    if any(ext in url_lower for ext in [".js", ".css", ".png", ".jpg", ".gif", ".svg", ".ico", ".woff"]):
        return False
    # Look for API-ish patterns
    api_indicators = ["api", "retailer", "locator", "location", "store", "search", "find", "where", "json", "data"]
    return any(ind in url_lower for ind in api_indicators)


def discover_state(page, state: dict) -> dict:
    code = state["code"]
    url = state["url"]
    zip_code = state["zip"]
    captured = []

    def on_request(request):
        req_url = request.url
        if is_api_call(req_url):
            captured.append({
                "url": req_url,
                "method": request.method,
                "post_data": request.post_data,
            })

    def on_response(response):
        resp_url = response.url
        if is_api_call(resp_url):
            try:
                ct = response.headers.get("content-type", "")
                if "json" in ct or "javascript" in ct:
                    # Already captured from request side, just note content type
                    pass
            except Exception:
                pass

    page.on("request", on_request)

    print(f"\n{'='*60}")
    print(f"[{code}] Visiting: {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # Try to find a zip/search input and type into it
        zip_inputs = page.query_selector_all(
            'input[type="text"], input[type="search"], input[placeholder*="zip" i], '
            'input[placeholder*="ZIP" i], input[placeholder*="city" i], '
            'input[name*="zip" i], input[id*="zip" i], input[aria-label*="zip" i]'
        )

        if zip_inputs:
            for inp in zip_inputs[:2]:
                try:
                    inp.fill(zip_code)
                    print(f"  Filled zip input with {zip_code}")
                    break
                except Exception:
                    continue
            # Press Enter or find submit button
            page.keyboard.press("Enter")
            time.sleep(3)

        # Also click any "search" / "find" / "locate" buttons
        buttons = page.query_selector_all(
            'button:has-text("Search"), button:has-text("Find"), button:has-text("Locate"), '
            'button:has-text("Go"), input[type="submit"]'
        )
        if buttons:
            try:
                buttons[0].click()
                time.sleep(3)
            except Exception:
                pass

    except Exception as e:
        print(f"  ERROR navigating {code}: {e}")

    page.remove_listener("request", on_request)

    return {
        "state": code,
        "url": url,
        "api_calls": captured,
    }


def main():
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        for state in STATES:
            result = discover_state(page, state)
            results.append(result)
            print(f"  Captured {len(result['api_calls'])} API calls")
            for call in result["api_calls"]:
                print(f"    [{call['method']}] {call['url']}")
                if call.get("post_data"):
                    print(f"      Body: {call['post_data'][:200]}")

        browser.close()

    # Save results
    with open("retailer_api_discovery.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n\nResults saved to retailer_api_discovery.json")


if __name__ == "__main__":
    main()
