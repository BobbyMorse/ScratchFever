"""Probe TN lottery winners with patient Cloudflare challenge handling."""
from __future__ import annotations
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

URLS = [
    "https://www.tnlottery.com/winners/",
    "https://www.tnlottery.com/winners-stories/",
    "https://www.tnlottery.com/big-winners/",
    "https://www.tnlottery.com/recent-winners/",
]


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/Chicago",
        )
        # Strip the obvious bot fingerprints.
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = { runtime: {} };
        """)

        for url in URLS:
            page = ctx.new_page()
            print(f"\n=== TRY {url} ===")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as e:
                print(f"  goto: {e}")
            # Give Cloudflare up to 20s to clear
            for _ in range(20):
                title = page.title()
                if "Just a moment" not in title and "verify" not in title.lower():
                    break
                page.wait_for_timeout(1000)
            print(f"  final url: {page.url}")
            print(f"  title: {page.title()}")
            html = page.content()
            print(f"  html: {len(html)} bytes")
            text = page.inner_text("body")[:500]
            print(f"  text[:500]: {text!r}")
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
