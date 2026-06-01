"""
Playwright-based recon for retailer-locator pages. For each state, we:
  1. Load the locator URL
  2. Fill the first text input we find with a state-specific zip
  3. Click the first submit-like button or press Enter
  4. Wait a few seconds, then dump every XHR/fetch we intercepted with:
        method, URL, status, content-type, response body preview

This is a one-shot exploratory script — run it locally, read the output,
hand-code each state's real scraper based on the discovered API.
"""
from __future__ import annotations
import json
import sys

from playwright.sync_api import sync_playwright

STATES = {
    "LA": {
        "url": "https://louisianalottery.com/where-to-play/",
        "search": "70112",
    },
    "ID": {
        "url": "https://www.idaholottery.com/pages/find-a-retailer",
        "search": "83702",
    },
    "MN": {
        "url": "https://www.mnlottery.com/retailers/find-a-retailer",
        "search": "55101",
    },
    "KS": {
        "url": "https://www.playonkansas.com/find-retailers",
        "search": "66603",
    },
    "MD": {
        "url": "https://rewards.mdlottery.com/retail/locator",
        "search": "21201",
    },
    "NM": {
        "url": "https://www.nmlottery.com/retailers/",
        "search": "87102",
    },
    "OH": {
        "url": "https://www.ohiolottery.com/retail-locations",
        "search": "43215",
    },
    "PA": {
        "url": "https://www.palottery.pa.gov/About-PA-Lottery/Retailers.aspx",
        "search": "17101",
    },
}

INTERESTING_KEYWORDS = (
    "retail", "store", "location", "locator", "dealer",
    "where", "find", "search", "outlet", "place",
)


def looks_interesting(url: str, content_type: str) -> bool:
    low = url.lower()
    if any(k in low for k in INTERESTING_KEYWORDS):
        return True
    return "json" in content_type.lower()


def recon_state(pw, code: str, cfg: dict) -> dict:
    out: dict = {"code": code, "url": cfg["url"], "xhrs": []}
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )
    page = ctx.new_page()

    intercepted: list[dict] = []

    def on_response(resp):
        try:
            url = resp.url
            req = resp.request
            if req.resource_type not in ("xhr", "fetch", "document"):
                return
            if req.resource_type == "document" and url != cfg["url"]:
                return
            ct = resp.headers.get("content-type", "")
            if not looks_interesting(url, ct):
                return
            body_preview = ""
            try:
                body = resp.body()
                body_preview = body[:1500].decode("utf-8", errors="replace")
            except Exception as e:
                body_preview = f"<body read error: {e}>"
            intercepted.append({
                "method": req.method,
                "url": url,
                "status": resp.status,
                "content_type": ct,
                "request_headers": dict(req.headers),
                "request_post_data": req.post_data,
                "body_preview": body_preview,
            })
        except Exception:
            pass

    page.on("response", on_response)

    try:
        page.goto(cfg["url"], wait_until="domcontentloaded", timeout=45_000)
    except Exception as e:
        out["error"] = f"goto: {e}"
        browser.close()
        out["xhrs"] = intercepted
        return out

    page.wait_for_timeout(2500)

    # Try to type the search query into any plausible input and submit.
    typed = False
    for selector in [
        "input[placeholder*='zip' i]",
        "input[placeholder*='address' i]",
        "input[placeholder*='city' i]",
        "input[placeholder*='location' i]",
        "input[id*='zip' i]",
        "input[id*='address' i]",
        "input[name*='zip' i]",
        "input[name*='address' i]",
        "input[type='search']",
        "input.field",
    ]:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                el.fill(cfg["search"])
                typed = True
                break
        except Exception:
            continue

    if typed:
        # Try clicking a search/find/submit button.
        for selector in [
            "button[type='submit']",
            "button:has-text('Search')",
            "button:has-text('Find')",
            "button:has-text('Locate')",
            "input[type='submit']",
            "a:has-text('Search')",
            "a:has-text('Find')",
            "[role='button']:has-text('Search')",
        ]:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    break
            except Exception:
                continue
        else:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

    page.wait_for_timeout(6000)
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    out["typed"] = typed
    out["xhrs"] = intercepted
    browser.close()
    return out


def main(only: list[str] | None = None) -> None:
    targets = STATES if not only else {k: v for k, v in STATES.items() if k in only}
    results = []
    with sync_playwright() as pw:
        for code, cfg in targets.items():
            print(f"=== {code} starting ===", flush=True)
            try:
                r = recon_state(pw, code, cfg)
            except Exception as e:
                r = {"code": code, "error": str(e), "xhrs": []}
            print(f"=== {code} done: {len(r.get('xhrs', []))} interesting requests ===", flush=True)
            results.append(r)

    out_path = "/tmp/recon_retailers.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {out_path} ({sum(len(r.get('xhrs', [])) for r in results)} total interesting requests)")

    # Quick summary
    for r in results:
        print(f"\n--- {r['code']} {r['url']} ---")
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
        for x in r.get("xhrs", []):
            print(f"  [{x['method']} {x['status']}] {x['url'][:140]}")
            if x.get("body_preview"):
                preview = x["body_preview"][:200].replace("\n", " ")
                print(f"    body: {preview}")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
