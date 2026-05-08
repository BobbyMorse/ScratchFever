"""
Fetch all Arizona Lottery retailers via the official API and save to az_retailers.csv.

API: https://api.arizonalottery.com/v2/retailers
     ?latitude=X&longitude=Y&distance=MILES&limit=40

Strategy: walk a lat/lng grid across Arizona with ~0.25-degree steps (~17 miles),
request the 40 nearest retailers within 20 miles of each grid point, deduplicate
by UUID.  Dense metros (Phoenix, Tucson) use a finer 0.15-degree sub-grid.

Usage:
    python fetch_az_retailers.py

Output:
    az_retailers.csv  (in project root)
"""

import csv
import math
import time
import logging
import requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH = Path(__file__).parent / "az_retailers.csv"
API_URL  = "https://api.arizonalottery.com/v2/retailers"

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.arizonalottery.com",
    "Referer": "https://www.arizonalottery.com/where-to-play/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Arizona bounding box
LAT_MIN, LAT_MAX = 31.33, 37.00
LNG_MIN, LNG_MAX = -114.83, -109.05

# Standard grid: ~0.28 deg ≈ 19 miles lat, ~15 miles lng at lat 34
GRID_STEP = 0.28

# Fine grid for dense metros (Phoenix and Tucson) — 0.12 deg ≈ 8 miles
FINE_STEP = 0.12
PHOENIX_BOX  = (33.15, 33.95, -112.50, -111.55)   # (lat_min, lat_max, lng_min, lng_max)
TUCSON_BOX   = (31.90, 32.45, -111.15, -110.70)

# Radius used per call (miles)
RADIUS = 18

FIELDNAMES = [
    "id", "name", "address", "city", "state", "zipCode",
    "phone", "latitude", "longitude",
]


def build_grid() -> list[tuple[float, float]]:
    points = set()

    # Standard grid over all of AZ
    lat = LAT_MIN
    while lat <= LAT_MAX + 0.01:
        lng = LNG_MIN
        while lng <= LNG_MAX + 0.01:
            points.add((round(lat, 4), round(lng, 4)))
            lng += GRID_STEP
        lat += GRID_STEP

    # Fine grid over Phoenix metro
    lat_min, lat_max, lng_min, lng_max = PHOENIX_BOX
    lat = lat_min
    while lat <= lat_max + 0.01:
        lng = lng_min
        while lng <= lng_max + 0.01:
            points.add((round(lat, 4), round(lng, 4)))
            lng += FINE_STEP
        lat += FINE_STEP

    # Fine grid over Tucson metro
    lat_min, lat_max, lng_min, lng_max = TUCSON_BOX
    lat = lat_min
    while lat <= lat_max + 0.01:
        lng = lng_min
        while lng <= lng_max + 0.01:
            points.add((round(lat, 4), round(lng, 4)))
            lng += FINE_STEP
        lat += FINE_STEP

    # Sort top-to-bottom, left-to-right for predictable progress logs
    return sorted(points, key=lambda p: (-p[0], p[1]))


def fetch_retailers(lat: float, lng: float, distance: int = RADIUS) -> list[dict]:
    try:
        resp = requests.get(
            API_URL,
            params={"latitude": lat, "longitude": lng, "distance": distance, "limit": 40},
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug("HTTP %d at (%.3f, %.3f)", resp.status_code, lat, lng)
            return []
        return resp.json() or []
    except Exception as e:
        logger.debug("Error at (%.3f, %.3f): %s", lat, lng, e)
        return []


def normalize(raw: dict) -> dict:
    return {
        "id":        str(raw.get("id", "")),
        "name":      raw.get("retailerName", "").strip(),
        "address":   raw.get("streetAddress", "").strip(),
        "city":      raw.get("city", "").strip(),
        "state":     "AZ",
        "zipCode":   str(raw.get("postalCode", "")).strip(),
        "phone":     "",
        "latitude":  str(raw.get("latitude", "")),
        "longitude": str(raw.get("longitude", "")),
    }


def main():
    grid = build_grid()
    logger.info("Grid: %d points (standard + Phoenix/Tucson fine grids)", len(grid))

    seen: dict[str, dict] = {}
    total_calls = 0
    cap_hits = 0   # points that returned exactly 40 (may be missing some retailers)

    for i, (lat, lng) in enumerate(grid):
        records = fetch_retailers(lat, lng)
        total_calls += 1

        new = 0
        for raw in records:
            r = normalize(raw)
            if r["id"] and r["id"] not in seen:
                seen[r["id"]] = r
                new += 1

        if len(records) == 40:
            cap_hits += 1

        if (i + 1) % 50 == 0 or i == len(grid) - 1:
            logger.info(
                "[%d/%d] unique so far: %d  (cap hits: %d)",
                i + 1, len(grid), len(seen), cap_hits,
            )

        # Polite delay
        time.sleep(0.2)

    logger.info("Total calls: %d  |  Unique retailers: %d  |  Cap hits: %d",
                total_calls, len(seen), cap_hits)

    if cap_hits > 0:
        logger.warning(
            "%d grid points hit the 40-result cap — a few retailers near "
            "those dense spots may be missing.", cap_hits
        )

    records = [r for r in seen.values() if r["name"]]
    records.sort(key=lambda r: (r["city"], r["name"]))

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)

    logger.info("Saved %d retailers → %s", len(records), OUT_PATH)


if __name__ == "__main__":
    main()
