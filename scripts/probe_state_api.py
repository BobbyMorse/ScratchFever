"""Generic API/XHR sniffer for a state lottery winners page.
Usage: python scripts/probe_state_api.py <url>
Filters out trackers/analytics; reports JSON/CSV responses with sample bodies."""
from __future__ import annotations
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

TRACKER_FRAGMENTS = (
    "google", "doubleclick", "facebook", "bing", "bat.",
    "ipredictive", "adentifi", "nr-data", "gtm", "newrelic",
    "exponea", "freshworks", "adobe", "adobedtm", "adsrvr", "adnxs",
    "linkedin", "tiktok", "yahoo", "criteo", "addtoany",
)


def main():
    if len(sys.argv) < 2:
        print("usage: probe_state_api.py <url>")
        sys.exit(1)
    url = sys.argv[1]

    api_calls = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        def on_response(resp):
            u = resp.url
            if any(t in u for t in TRACKER_FRAGMENTS):
                return
            ctype = (resp.headers.get("content-type") or "").lower()
            if not ("json" in ctype or "csv" in ctype or "xml" in ctype):
                return
            body = None
            try:
                body = resp.body()[:600].decode("utf-8", errors="replace")
            except Exception:
                body = "<err reading body>"
            api_calls.append({
                "url": u, "status": resp.status, "ctype": ctype,
                "sample": body,
            })

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=60_000)
        except Exception as e:
            print(f"goto err: {e}")
        page.wait_for_timeout(3000)
        browser.close()

    print(f"=== {len(api_calls)} JSON/CSV/XML responses ===\n")
    for c in api_calls:
        print(f"[{c['status']}] {c['ctype'][:30]:30s} {c['url']}")
        sample = c['sample'].replace("\n", " ")[:300]
        print(f"  sample: {sample}")
        print()


if __name__ == "__main__":
    main()
