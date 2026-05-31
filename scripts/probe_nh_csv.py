"""Probe NH winners page for the CSV download endpoint."""
from __future__ import annotations
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def main():
    captured_urls = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, accept_downloads=True)
        page = ctx.new_page()

        def on_request(req):
            url = req.url
            if any(s in url.lower() for s in ("csv", "winners", "api", "download")) and "nhlottery" in url.lower():
                captured_urls.append(f"{req.method} {url}")
        page.on("request", on_request)

        page.goto("https://www.nhlottery.com/winning/winners", wait_until="networkidle", timeout=60_000)
        print(f"Title: {page.title()}")
        # Find CSV download link/button
        for sel in [
            "a:has-text('CSV')",
            "button:has-text('CSV')",
            "a:has-text('Download')",
            "button:has-text('Download')",
            "[href*='csv' i]",
            "[href*='download' i]",
        ]:
            try:
                n = page.locator(sel).count()
                if n:
                    print(f"  {sel}: {n}")
                    for i in range(min(n, 3)):
                        el = page.locator(sel).nth(i)
                        href = el.get_attribute("href")
                        text = el.text_content()
                        print(f"    [{i}] text={text!r}  href={href!r}")
            except Exception as e:
                print(f"  {sel}: err {e}")

        # Try clicking the Download CSV button and capture the download
        print("\n=== CLICK CSV DOWNLOAD ===")
        try:
            btn = page.locator("a:has-text('Download Results as a CSV')").first
            with page.expect_download(timeout=30000) as dl_info:
                btn.click()
            dl = dl_info.value
            print(f"  Download URL: {dl.url}")
            path = f"scripts/_nh_winners_sample.csv"
            dl.save_as(path)
            print(f"  Saved to {path}")
            # Print first 20 lines
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= 20:
                        break
                    print(f"    {line.rstrip()}")
        except Exception as e:
            print(f"  CSV click failed: {e}")

        print("\n=== Captured winner/api/csv URLs ===")
        for u in captured_urls[:30]:
            print(f"  {u}")

        browser.close()


if __name__ == "__main__":
    main()
