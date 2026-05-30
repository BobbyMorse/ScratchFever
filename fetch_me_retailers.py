"""
Fetch all Maine Lottery retailers by iterating every Maine ZIP code through the
official "Find a Retailer" CGI and merging the results.

Source form: https://www.mainelottery.com/players_info/where_to_buy.html
Endpoint:    POST https://www.mainelottery.com/cgi/findAgent.pl  (zipcode=NNNNN)

The endpoint returns an HTML table with columns:
    Store Name | Street Address | Town | Zip | Phone | Tickets Sold (games)

Maine ZIPs span 03901-04999. Invalid ones return "No records found" — cheap.
Retailers are deduplicated by (name, address, zip).

Usage:
    python fetch_me_retailers.py
"""

import asyncio
import csv
import logging
import re
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH = Path(__file__).parent / "me_retailers.csv"
ENDPOINT = "https://www.mainelottery.com/cgi/findAgent.pl"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.mainelottery.com/players_info/where_to_buy.html",
    "Origin": "https://www.mainelottery.com",
}

FIELDNAMES = ["name", "address", "city", "zip_code", "phone", "games", "state"]

# Maine ZIP range — 03901 through 04999 covers all of Maine (and a few NH/MA
# ZIPs near the border which simply return "No records found").
ZIP_MIN, ZIP_MAX = 3901, 4999

WORKERS = 20
TIMEOUT_S = 20
RETRIES = 3

_PHONE_RE = re.compile(r"\(?(\d{3})\)?\s*[-.\s]?\s*(\d{3})\s*[-.\s]?\s*(\d{4})")


def normalize_phone(raw: str) -> str:
    """Maine returns '(207) 772 - 1360' with stray spaces — collapse to '(207) 772-1360'."""
    m = _PHONE_RE.search(raw or "")
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if m else (raw or "").strip()


def clean_name(name: str) -> str:
    """Maine encodes apostrophes as backticks (e.g. JOE`S). Restore them."""
    return (name or "").replace("`", "'").strip()


def parse_retailers(html: str) -> list[dict]:
    """Extract retailer rows from the findAgent.pl response."""
    if "No records found" in html:
        return []

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="zebra") or soup.find("table", class_="tbstriped")
    if table is None:
        return []

    out: list[dict] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue  # header rows have <th>, no <td>

        name    = clean_name(cells[0].get_text(strip=True))
        address = cells[1].get_text(strip=True)
        city    = cells[2].get_text(strip=True)
        zip_    = cells[3].get_text(strip=True)
        phone   = normalize_phone(cells[4].get_text(strip=True))
        games   = cells[5].get_text(strip=True) if len(cells) > 5 else ""

        if not name or not zip_:
            continue
        out.append({
            "name":     name,
            "address":  address,
            "city":     city,
            "zip_code": zip_,
            "phone":    phone,
            "games":    games,
            "state":    "ME",
        })
    return out


async def fetch_zip(
    client: httpx.AsyncClient,
    zip_code: str,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    async with semaphore:
        for attempt in range(RETRIES):
            try:
                r = await client.post(
                    ENDPOINT,
                    data={"zipcode": zip_code},
                    headers=HEADERS,
                    timeout=TIMEOUT_S,
                )
                if r.status_code == 200:
                    return parse_retailers(r.text)
                if r.status_code == 429:
                    await asyncio.sleep(int(r.headers.get("Retry-After", 5)))
                else:
                    logger.debug("zip %s HTTP %d", zip_code, r.status_code)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                logger.debug("zip %s error (attempt %d): %s", zip_code, attempt + 1, e)
                if attempt < RETRIES - 1:
                    await asyncio.sleep(1 + attempt)
        return []


async def scrape() -> list[dict]:
    zips = [f"{z:05d}" for z in range(ZIP_MIN, ZIP_MAX + 1)]
    total = len(zips)
    logger.info("Querying %d Maine ZIPs with %d workers", total, WORKERS)

    semaphore = asyncio.Semaphore(WORKERS)
    seen: set[tuple[str, str, str]] = set()
    retailers: list[dict] = []
    completed = 0
    t0 = time.time()

    async with httpx.AsyncClient(http2=False) as client:
        tasks = [fetch_zip(client, z, semaphore) for z in zips]
        for coro in asyncio.as_completed(tasks):
            results = await coro
            for r in results:
                key = (r["name"].upper(), r["address"].upper(), r["zip_code"])
                if key in seen:
                    continue
                seen.add(key)
                retailers.append(r)

            completed += 1
            if completed % 50 == 0 or completed == total:
                elapsed = time.time() - t0
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (total - completed) / rate if rate > 0 else 0
                print(
                    f"  {completed}/{total} zips  "
                    f"| {len(retailers)} unique retailers  "
                    f"| {rate:.1f} zips/s  "
                    f"| ETA {eta:.0f}s",
                    end="\r",
                )
    print()
    return retailers


def save_csv(retailers: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(retailers)
    logger.info("Saved %d ME retailers -> %s", len(retailers), path)


async def main() -> None:
    t0 = time.time()
    retailers = await scrape()
    retailers.sort(key=lambda r: (r["zip_code"], r["name"].lower()))
    save_csv(retailers, OUT_PATH)
    logger.info("Done in %.0fs", time.time() - t0)


if __name__ == "__main__":
    asyncio.run(main())
