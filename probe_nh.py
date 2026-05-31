"""One-off NH retailer-locator probe.

Loads nhlottery.com/find-retailer, fills the zip search with 03301 (Concord),
and prints every XHR/fetch that fires — first-party AND third-party.
Goal: find the real retailer-lookup endpoint that the existing scraper's
interceptor is missing.
"""
import json
import time
from playwright.sync_api import sync_playwright

LOCATOR_URL = "https://www.nhlottery.com/find-retailer"
TEST_ZIP = "03301"

calls: list[dict] = []

def on_request(req):
    if req.resource_type in ("xhr", "fetch"):
        calls.append({
            "method": req.method,
            "url": req.url,
            "type": req.resource_type,
            "post": (req.post_data or "")[:300],
            "headers": dict(req.headers),
        })

def on_response(resp):
    if resp.request.resource_type in ("xhr", "fetch"):
        try:
            body = resp.text()[:600]
        except Exception:
            body = "(could not read body)"
        for c in calls:
            if c["url"] == resp.url and "body" not in c:
                c["body"] = body
                c["status"] = resp.status
                c["ctype"] = resp.headers.get("content-type", "")
                break

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="en-US",
    )
    page = ctx.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    print(f"loading {LOCATOR_URL} ...")
    page.goto(LOCATOR_URL, wait_until="domcontentloaded", timeout=30_000)

    # Dump every input on the page to see what we're working with
    print("\n=== INPUTS ON PAGE ===")
    for inp in page.query_selector_all("input"):
        try:
            attrs = inp.evaluate("e => ({name: e.name, type: e.type, placeholder: e.placeholder, id: e.id})")
            print(" ", attrs)
        except Exception as e:
            print("  err:", e)

    print("\n=== BUTTONS ON PAGE ===")
    for btn in page.query_selector_all("button"):
        try:
            txt = btn.inner_text().strip()
            if txt:
                print(" ", repr(txt))
        except Exception:
            pass

    # Try a few search strategies
    print(f"\n=== TYPING {TEST_ZIP} ===")
    try:
        inp = (
            page.query_selector("input[name*='zip' i]") or
            page.query_selector("input[placeholder*='zip' i]") or
            page.query_selector("input[placeholder*='city' i]") or
            page.query_selector("input[placeholder*='location' i]") or
            page.query_selector("input[type='text']") or
            page.query_selector("input[type='search']")
        )
        if inp:
            print("  matched input:", inp.evaluate("e => e.outerHTML")[:200])
            inp.click()
            inp.fill(TEST_ZIP)
            time.sleep(0.5)
            inp.press("Enter")
        else:
            print("  NO INPUT MATCHED")
    except Exception as e:
        print("  error filling input:", e)

    # Wait for any triggered network activity
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    time.sleep(3)

    # Also try a button click as a backup
    print("\n=== CLICKING ANY SEARCH BUTTON ===")
    try:
        for sel in ["button:has-text('Search')", "button:has-text('Find')", "button[type='submit']", "input[type='submit']"]:
            b = page.query_selector(sel)
            if b:
                print(f"  clicking {sel}")
                b.click()
                time.sleep(2)
                break
    except Exception as e:
        print("  no button:", e)

    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    browser.close()

print(f"\n=== GAMBYTSERVICES CALLS (the retailer API) ===\n")
for i, c in enumerate(calls):
    if "gambytservices" not in c["url"]:
        continue
    print(f"[{i}] {c['method']} {c['url']}")
    print(f"    status={c.get('status')}  ctype={c.get('ctype','')}")
    print(f"    headers:")
    for k, v in c.get("headers", {}).items():
        print(f"      {k}: {v}")
    if c.get("post"):
        print(f"    post: {c['post']}")
    body = c.get("body", "")
    if body:
        print(f"    body[:400]: {body[:400]}")
    print()
