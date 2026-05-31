"""Capture the request headers NH sends to the winners JSON API."""
from __future__ import annotations
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def main():
    snapshots = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        def on_request(req):
            if "gambytservices.com" in req.url and "winners" in req.url:
                snapshots.append({"url": req.url, "method": req.method, "headers": dict(req.headers)})

        page.on("request", on_request)
        page.goto("https://www.nhlottery.com/winning/winners", wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(3000)
        browser.close()

    print(f"=== Captured {len(snapshots)} winners-API requests ===\n")
    for s in snapshots[:3]:
        print(f"URL: {s['url']}")
        print(f"METHOD: {s['method']}")
        print("HEADERS:")
        for k, v in s["headers"].items():
            print(f"  {k}: {v}")
        print()


if __name__ == "__main__":
    main()
