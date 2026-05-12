"""
Test: extract session cookies from Playwright, then use requests for paginated calls.
"""
import json
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://ris.p1.awc.lotteryservices.net"
SEED_URL = "https://www.rilot.com/en-us/player-zone/find-a-retailer.html"


def get_session_cookies():
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
        page.goto(SEED_URL, wait_until="networkidle", timeout=30_000)
        cookies = ctx.cookies()
        browser.close()

    # Build a requests.Session with those cookies
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.rilot.com/",
        "Origin": "https://www.rilot.com",
    })
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

    print(f"Got {len(cookies)} cookies: {[c['name'] for c in cookies]}")
    return session


def test_city(session, city):
    r = session.get(
        f"{BASE_URL}/api/v1/locations",
        params={"city": city},
        timeout=15,
    )
    print(f"  city={city!r}: status={r.status_code}")
    if r.status_code == 200:
        data = r.json()
        total = data.get("nextItems", 0) + 10
        print(f"  nextItems={data.get('nextItems')}, locations={len(data.get('locations', []))}")
        print(f"  nextPageUrl: {data.get('nextPageUrl')}")
        return data
    else:
        print(f"  Response: {r.text[:200]}")
        return None


def test_pagination(session, next_path, seed, city, size=100):
    """Try large page size."""
    r = session.get(
        f"{BASE_URL}/api/v1/locations/page",
        params={"city": city, "page": 1, "size": size, "seed": seed},
        timeout=15,
    )
    print(f"  size={size}: status={r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"  locations={len(data.get('locations', []))}, nextItems={data.get('nextItems')}")
    else:
        print(f"  Response: {r.text[:200]}")


if __name__ == "__main__":
    session = get_session_cookies()

    data = test_city(session, "Providence")
    if data:
        # Test large page size
        from urllib.parse import urlparse, parse_qs
        next_url = data.get("nextPageUrl", "")
        params = parse_qs(urlparse(next_url).query)
        seed = params.get("seed", [None])[0]
        if seed:
            print(f"\nTesting large size (seed={seed}):")
            test_pagination(session, next_url, seed, "Providence", size=500)

    print("\nTesting other cities:")
    for city in ["Warwick", "Woonsocket", "Pawtucket"]:
        test_city(session, city)
