"""
Fetch all Florida Lottery retailers via the official API and save to fl_retailers.csv.

API: https://apim-website-prod-eastus.azure-api.net/searchretailersapp/searchRetailers
     ?geoLat=X&geoLong=Y&radius=MILES

Strategy: walk a lat/lng grid across Florida with 0.22-degree steps (~15 miles),
request all retailers within 15 miles of each grid point, deduplicate by LocationId.
Dense metros (Miami, Fort Lauderdale, Tampa, Orlando, Jacksonville) use a finer
0.10-degree sub-grid.

Usage:
    python fetch_fl_retailers.py

Output:
    fl_retailers.csv  (in project root)
"""

import csv
import time
import logging
import requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH = Path(__file__).parent / "fl_retailers.csv"
API_URL  = "https://apim-website-prod-eastus.azure-api.net/searchretailersapp/searchRetailers"

HEADERS = {
    "Accept": "application/json",
    "x-partner": "web",
    "Origin": "https://www.floridalottery.com",
    "Referer": "https://floridalottery.com/where-to-play",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Florida bounding box
LAT_MIN, LAT_MAX = 24.40, 31.00
LNG_MIN, LNG_MAX = -87.65, -80.03

GRID_STEP = 0.22   # ~15 miles lat, ~12 miles lng at lat 27 — fits inside RADIUS
FINE_STEP  = 0.10  # ~7 miles — for dense metros
RADIUS     = 15    # miles per query

# Dense metro bounding boxes: (lat_min, lat_max, lng_min, lng_max)
METRO_BOXES = {
    "Miami/Fort Lauderdale": (25.50, 26.90, -80.55, -80.05),
    "Tampa/St. Pete":        (27.65, 28.15, -82.90, -82.20),
    "Orlando":               (28.25, 28.75, -81.65, -80.95),
    "Jacksonville":          (30.05, 30.55, -82.05, -81.35),
}

FIELDNAMES = [
    "id", "name", "address", "city", "state", "zipCode",
    "phone", "latitude", "longitude",
]


def build_grid() -> list[tuple[float, float]]:
    points: set[tuple[float, float]] = set()

    lat = LAT_MIN
    while lat <= LAT_MAX + 0.01:
        lng = LNG_MIN
        while lng <= LNG_MAX + 0.01:
            points.add((round(lat, 4), round(lng, 4)))
            lng += GRID_STEP
        lat += GRID_STEP

    for label, (lat_min, lat_max, lng_min, lng_max) in METRO_BOXES.items():
        lat = lat_min
        while lat <= lat_max + 0.01:
            lng = lng_min
            while lng <= lng_max + 0.01:
                points.add((round(lat, 4), round(lng, 4)))
                lng += FINE_STEP
            lat += FINE_STEP

    return sorted(points, key=lambda p: (-p[0], p[1]))


def fetch_retailers(lat: float, lng: float) -> list[dict]:
    try:
        resp = requests.get(
            API_URL,
            params={"geoLat": lat, "geoLong": lng, "radius": RADIUS},
            headers=HEADERS,
            timeout=20,
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
        "id":        str(raw.get("LocationId", "")),
        "name":      (raw.get("Name") or "").strip().title(),
        "address":   (raw.get("Address") or "").strip().title(),
        "city":      (raw.get("City") or "").strip().title(),
        "state":     "FL",
        "zipCode":   str(raw.get("Zip") or "").strip(),
        "phone":     str(raw.get("LocationPhoneNbr") or "").strip(),
        "latitude":  str(raw.get("GeoLat") or "").strip(),
        "longitude": str(raw.get("GeoLong") or "").strip(),
    }


def main():
    grid = build_grid()
    logger.info("Grid: %d points (standard + metro fine grids)", len(grid))

    seen: dict[str, dict] = {}

    for i, (lat, lng) in enumerate(grid):
        records = fetch_retailers(lat, lng)

        for raw in records:
            rid = str(raw.get("LocationId", ""))
            if rid and rid not in seen:
                seen[rid] = normalize(raw)

        if (i + 1) % 100 == 0 or i == len(grid) - 1:
            logger.info("[%d/%d] unique so far: %d", i + 1, len(grid), len(seen))

        time.sleep(0.18)

    logger.info("Done. Unique retailers: %d", len(seen))

    records_out = [r for r in seen.values() if r["name"]]
    records_out.sort(key=lambda r: (r["city"], r["name"]))

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records_out)

    logger.info("Saved %d retailers → %s", len(records_out), OUT_PATH)


if __name__ == "__main__":
    main()
