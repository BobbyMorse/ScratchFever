"""
Fetch all Georgia Lottery retailers and save to ga_retailers.csv.

API: https://www.galottery.com/api/v1/locations  (AWC-style, same pattern as RI)

Strategy: the API returns all ~8900 GA retailers regardless of the zip/city
parameter used. We query once to obtain a session seed, then paginate through
every page (10 results per page, ~893 pages total) and deduplicate by ID.

Usage:
    python fetch_ga_retailers.py

Output:
    ga_retailers.csv
"""

import csv
import time
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH  = Path(__file__).parent / "ga_retailers.csv"
BASE_URL  = "https://www.galottery.com/api/v1/locations"
PAGE_URL  = "https://www.galottery.com/api/v1/locations/page"
PAGE_SIZE = 10  # API hard-caps at 10

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.galottery.com",
    "Referer": "https://www.galottery.com/en-us/player-zone/where-to-play.html",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

FIELDNAMES = ["id", "name", "address", "city", "state", "zipCode", "phone", "latitude", "longitude"]


def get(url: str, params: dict) -> dict | None:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            ct = r.headers.get("content-type", "")
            if "json" not in ct and not r.text.strip().startswith(("{", "[")):
                logger.debug("Non-JSON response at %s: %s", url, r.text[:100])
                return None
            return r.json()
        logger.debug("HTTP %d for %s %s", r.status_code, url, params)
    except Exception as e:
        logger.debug("Request error: %s", e)
    return None


def fresh_seed() -> tuple[str, list[dict]]:
    """Get a new session seed and the first page of results."""
    d0 = get(BASE_URL, {"zip": "30301"})
    if not d0:
        raise RuntimeError("Failed to reach GA lottery API")
    seed = parse_qs(urlparse(d0.get("nextPageUrl", "")).query).get("seed", [None])[0]
    if not seed:
        raise RuntimeError("Could not extract seed")
    return seed, d0.get("locations", [])


def normalize(raw: dict) -> dict:
    return {
        "id":        str(raw.get("id") or ""),
        "name":      (raw.get("name") or "").strip().title(),
        "address":   (raw.get("address1") or raw.get("address") or "").strip().title(),
        "city":      (raw.get("city") or "").strip().title(),
        "state":     "GA",
        "zipCode":   str(raw.get("postalCode") or raw.get("zipCode") or raw.get("zip") or "").strip(),
        "phone":     str(raw.get("phoneNumber") or raw.get("phone") or "").strip(),
        "latitude":  str(raw.get("lattitude") or raw.get("latitude") or raw.get("lat") or ""),
        "longitude": str(raw.get("longitude") or raw.get("lng") or ""),
    }


def main():
    # Seed request — any zip works since the API returns all GA retailers regardless
    logger.info("Fetching page 0 to get session seed …")
    d0 = get(BASE_URL, {"zip": "30301"})
    if not d0:
        raise RuntimeError("Failed to reach GA lottery API")

    next_items = d0.get("nextItems", 0)
    total_est  = next_items + len(d0.get("locations", []))
    next_url   = d0.get("nextPageUrl", "")
    seed       = parse_qs(urlparse(next_url).query).get("seed", [None])[0]

    if not seed:
        raise RuntimeError(f"Could not extract seed from nextPageUrl: {next_url!r}")

    logger.info("Estimated total retailers: %d  |  seed: %s", total_est, seed)

    seen: dict[str, dict] = {}
    for raw in d0.get("locations", []):
        rid = raw.get("id")
        if rid:
            seen[str(rid)] = normalize(raw)

    # Paginate
    page = 1
    consecutive_empty = 0
    while True:
        data = get(PAGE_URL, {"zip": "30301", "page": page, "size": PAGE_SIZE, "seed": seed})
        if not data:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                logger.warning("3 consecutive failures — stopping at page %d", page)
                break
            time.sleep(1)
            page += 1
            continue

        consecutive_empty = 0
        locs = data.get("locations", [])
        new  = 0
        for raw in locs:
            rid = raw.get("id")
            if rid and str(rid) not in seen:
                seen[str(rid)] = normalize(raw)
                new += 1

        remaining = data.get("nextItems", 0)

        if page % 100 == 0 or not locs:
            logger.info(
                "page %d  got=%d  new=%d  total=%d  remaining=%d",
                page, len(locs), new, len(seen), remaining,
            )

        if not locs or remaining == 0:
            break

        page += 1
        time.sleep(0.12)

    logger.info("Done. Unique retailers: %d  (across %d pages)", len(seen), page)

    records = [r for r in seen.values() if r["name"]]
    records.sort(key=lambda r: (r["city"], r["name"]))

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)

    logger.info("Saved %d retailers → %s", len(records), OUT_PATH)


if __name__ == "__main__":
    main()
