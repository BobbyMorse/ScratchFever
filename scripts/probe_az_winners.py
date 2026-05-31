"""One-off probe: inspect arizonalottery.com/winners/ via Playwright to design
the scraper. Prints page structure clues (URL after redirects, presence of
winner cards/tables, pagination, API XHR calls, key selectors)."""
from __future__ import annotations
import json
import re
from playwright.sync_api import sync_playwright

URL = "https://www.arizonalottery.com/winners/"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def main():
    xhrs: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="en-US")
        page = ctx.new_page()

        def on_response(resp):
            url = resp.url.lower()
            if "arizonalottery.com" not in url:
                return
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" in ctype or "xml" in ctype or "/api/" in url or "wp-json" in url or "winners" in url:
                xhrs.append({"url": resp.url, "status": resp.status, "ctype": ctype})

        page.on("response", on_response)

        try:
            page.goto(URL, wait_until="networkidle", timeout=60_000)
        except Exception as e:
            print(f"goto error: {e}")

        print("=== FINAL URL ===")
        print(page.url)
        print()

        print("=== TITLE ===")
        print(page.title())
        print()

        html = page.content()
        print(f"=== HTML LENGTH === {len(html)} bytes")

        # Look for winner-like elements
        text = page.inner_text("body")
        print(f"=== BODY TEXT LENGTH === {len(text)} chars")
        print("=== FIRST 2000 BODY TEXT CHARS ===")
        print(text[:2000])
        print()

        # Check for common patterns
        prize_hits = re.findall(r"\$[\d,]+", text)
        print(f"=== $ MENTIONS === {len(prize_hits)} found, first 20: {prize_hits[:20]}")
        print()

        # Selector probes
        print("=== SELECTOR COUNTS ===")
        for sel in [
            "table",
            "tr",
            "[class*='winner']",
            "[class*='Winner']",
            "[data-game]",
            "[class*='card']",
            "article",
            ".grid",
            "img[src*='winner']",
            "a[href*='winner']",
        ]:
            try:
                cnt = page.locator(sel).count()
                print(f"  {sel}: {cnt}")
            except Exception as e:
                print(f"  {sel}: err {e}")
        print()

        # Pagination
        for sel in ["[aria-label*='page' i]", ".pagination", "button:has-text('Load more')",
                    "button:has-text('Next')", "a:has-text('Next')"]:
            try:
                cnt = page.locator(sel).count()
                if cnt:
                    print(f"PAGINATION? {sel}: {cnt}")
            except Exception:
                pass

        print()
        print("=== INTERESTING XHRS ===")
        for x in xhrs[:30]:
            print(f"  [{x['status']}] {x['ctype']:30s} {x['url']}")

        # Save HTML snippet for offline review
        with open("scripts/_az_winners_probe.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("\nSaved full HTML to scripts/_az_winners_probe.html")

        browser.close()


if __name__ == "__main__":
    main()
