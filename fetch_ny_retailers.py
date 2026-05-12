"""
Fetch all New York Lottery retailers via the Drupal API and save to ny_retailers.csv.

API: https://nylottery.ny.gov/drupal-api/api/retailers?_format=json&page=N
     6 results per page, ~2175 pages, ~13048 total retailers

Strategy: iterate all pages sequentially, deduplicate by (name, address, zipcode).

Usage:
    python fetch_ny_retailers.py

Output:
    ny_retailers.csv  (in project root)
"""

import csv
import time
import logging
import requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH = Path(__file__).parent / "ny_retailers.csv"
API_URL  = "https://nylottery.ny.gov/drupal-api/api/retailers"

HEADERS = {
    "Accept": "application/json",
    "Referer": "https://nylottery.ny.gov/find-a-retailer",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

FIELDNAMES = [
    "id", "name", "address", "city", "state", "zipCode",
    "phone", "latitude", "longitude",
]


def fetch_page(page: int) -> tuple[list[dict], int]:
    """Returns (rows, total_pages). total_pages is 0 on error."""
    try:
        resp = requests.get(
            API_URL,
            params={"_format": "json", "page": page},
            headers=HEADERS,
            timeout=20,
        )
        if resp.status_code != 200:
            logger.debug("HTTP %d on page %d", resp.status_code, page)
            return [], 0
        data = resp.json()
        rows = data.get("rows") or []
        pager = data.get("pager") or {}
        total_pages = int(pager.get("total_pages") or 0)
        return rows, total_pages
    except Exception as e:
        logger.debug("Error on page %d: %s", page, e)
        return [], 0


def normalize(raw: dict) -> dict:
    geo = str(raw.get("field_geofield") or "")
    lat, lng = "", ""
    if "," in geo:
        parts = geo.split(",", 1)
        lat = parts[0].strip()
        lng = parts[1].strip()

    return {
        "id":        "",
        "name":      str(raw.get("title") or "").strip(),
        "address":   str(raw.get("field_street_address") or "").strip(),
        "city":      str(raw.get("field_city") or "").strip(),
        "state":     str(raw.get("field_state") or "NY").strip(),
        "zipCode":   str(raw.get("field_zipcode") or "").strip(),
        "phone":     "",
        "latitude":  lat,
        "longitude": lng,
    }


def dedup_key(r: dict) -> tuple:
    return (r["name"].upper(), r["address"].upper(), r["zipCode"])


def main():
    # Discover total pages from page 0
    rows0, total_pages = fetch_page(0)
    if total_pages == 0:
        logger.error("Failed to fetch page 0 — aborting.")
        return
    logger.info("Total pages: %d", total_pages)

    seen: dict[tuple, dict] = {}

    def process_rows(rows: list[dict]):
        for raw in rows:
            r = normalize(raw)
            if not r["name"]:
                continue
            key = dedup_key(r)
            if key not in seen:
                seen[key] = r

    process_rows(rows0)

    for page in range(1, total_pages):
        rows, _ = fetch_page(page)
        process_rows(rows)

        if page % 100 == 0 or page == total_pages - 1:
            logger.info("[%d/%d] unique so far: %d", page, total_pages - 1, len(seen))

        time.sleep(0.15)

    logger.info("Total unique retailers: %d", len(seen))

    records = sorted(seen.values(), key=lambda r: (r["city"], r["name"]))

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)

    logger.info("Saved %d retailers → %s", len(records), OUT_PATH)


if __name__ == "__main__":
    main()
