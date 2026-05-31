"""Capture every JSON/XHR request made by NH winners page."""
from __future__ import annotations
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def main():
    api_calls = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        def on_response(resp):
            url = resp.url
            # Filter out trackers/analytics
            if any(t in url for t in (
                "google", "doubleclick", "facebook", "bing", "bat.",
                "ipredictive", "adentifi", "nr-data", "gtm", "newrelic",
                "exponea", "tk.nhlottery", "gamesrv1")):
                return
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" in ctype or "csv" in ctype:
                api_calls.append({"url": url, "status": resp.status, "ctype": ctype})

        page.on("response", on_response)
        page.goto("https://www.nhlottery.com/winning/winners", wait_until="networkidle", timeout=60_000)
        # Give it time for any deferred XHR
        page.wait_for_timeout(3000)
        # Try clicking "Filter" to trigger a fetch
        try:
            page.locator("button:has-text('Filter')").first.click(timeout=5000)
            page.wait_for_timeout(3000)
        except Exception as e:
            print(f"filter click skipped: {e}")

        # Try the Download CSV button
        try:
            with page.expect_download(timeout=15000) as dl_info:
                page.locator("button:has-text('Download Results as a CSV')").first.click()
            dl = dl_info.value
            print(f"\n=== CSV DOWNLOAD CAPTURED ===")
            print(f"URL: {dl.url}")
            path = "scripts/_nh_winners.csv"
            dl.save_as(path)
            with open(path, encoding="utf-8") as f:
                head = [next(f) for _ in range(10)]
            print("First 10 lines:")
            for line in head:
                print(f"  {line.rstrip()}")
            import os
            print(f"Size: {os.path.getsize(path):,} bytes")
        except Exception as e:
            print(f"CSV click failed: {e}")

        browser.close()

    print(f"\n=== {len(api_calls)} JSON/CSV API calls captured ===")
    for c in api_calls:
        print(f"  [{c['status']}] {c['ctype']:30s} {c['url']}")


if __name__ == "__main__":
    main()
