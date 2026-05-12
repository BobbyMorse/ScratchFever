"""
Quick discovery: visit the RI retailer finder page, intercept all API calls.
"""
import json
import time
from playwright.sync_api import sync_playwright

URL = "https://www.rilot.com/en-us/player-zone/find-a-retailer.html"
ZIP = "02831"

def main():
    captured = []

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

        def on_request(req):
            url = req.url
            skip = [".css", ".woff", ".png", ".jpg", ".svg", ".ico", ".gif",
                    "google-analytics", "googletagmanager", "facebook", "hotjar"]
            if any(s in url.lower() for s in skip):
                return
            captured.append({
                "method": req.method,
                "url": url,
                "post_data": req.post_data,
            })

        page.on("request", on_request)

        # Load with zip param in URL
        page.goto(f"{URL}?zipCity={ZIP}", wait_until="networkidle", timeout=30_000)
        time.sleep(3)

        # Also try filling the search box
        try:
            inputs = page.query_selector_all(
                'input[type="text"], input[type="search"], '
                'input[placeholder*="zip" i], input[placeholder*="city" i], '
                'input[name*="zip" i], input[id*="zip" i]'
            )
            if inputs:
                inputs[0].fill(ZIP)
                page.keyboard.press("Enter")
                time.sleep(3)
                print(f"Filled input with {ZIP} and pressed Enter")
        except Exception as e:
            print(f"Input fill error: {e}")

        browser.close()

    print(f"\nCaptured {len(captured)} requests\n")
    for r in captured:
        print(f"  [{r['method']}] {r['url']}")
        if r.get("post_data"):
            print(f"    Body: {r['post_data'][:500]}")

    with open("ri_retailer_api_discovery.json", "w") as f:
        json.dump(captured, f, indent=2)
    print("\nSaved to ri_retailer_api_discovery.json")


if __name__ == "__main__":
    main()
