"""
MA Retailer Enricher
====================
Adds four enrichment layers to ma_retailers.csv → ma_retailers_enriched.csv

  1. ZIP population density  (Census ACS 5-yr + ZCTA Gazetteer — free, no key)
  2. Interstate proximity     (OpenStreetMap Overpass API + Shapely — free, no key)
  3. Expanded name patterns   (code-only, instant)
  4. Google Places            (review count, hours, category — optional, API key)

Usage:
  python enricher.py                         # layers 1-3 only
  python enricher.py --gmaps YOUR_KEY        # + Google Places (layer 4)
  python enricher.py --in other.csv          # custom input CSV
  python enricher.py --workers 8             # Google Places concurrency
"""

import argparse
import asyncio
import csv
import io
import json
import math
import sys
import time
import zipfile
from pathlib import Path

import httpx
import requests
from shapely.geometry import MultiLineString, Point
from shapely.ops import nearest_points

# ── Constants ─────────────────────────────────────────────────────────────────

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2020_Gazetteer/2020_Gaz_zcta_national.zip"
)
ACS_URL = "https://api.census.gov/data/2021/acs/acs5"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_HEADERS = {
    "User-Agent": "ScratchFever/1.0 (research project)",
    "Content-Type": "application/x-www-form-urlencoded",
}

# Only MA+border interstates
INTERSTATE_REFS = {
    "I 90", "I 91", "I 93", "I 95",
    "I 190", "I 195", "I 290", "I 291", "I 395", "I 495", "I 84",
}

GMAPS_FIND_URL    = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
GMAPS_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

MA_ZIP_PREFIXES = ("01", "02")


# ── 1. ZIP POPULATION DENSITY ─────────────────────────────────────────────────

def fetch_zip_density() -> dict[str, float]:
    """Returns {zip_code: people_per_sq_mile}"""
    print("  Downloading ZCTA land-area Gazetteer…")
    r = requests.get(GAZETTEER_URL, timeout=60)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    lines = z.read(z.namelist()[0]).decode("utf-8").splitlines()
    area: dict[str, float] = {}
    for row in csv.DictReader(lines, delimiter="\t"):
        geoid = row["GEOID"].strip()
        sqmi  = row["ALAND_SQMI"].strip()
        if sqmi and float(sqmi) > 0:
            area[geoid] = float(sqmi)

    print(f"  Fetched {len(area)} ZCTA land-area records")

    print("  Fetching ZCTA population from Census ACS 5-year…")
    r2 = requests.get(
        ACS_URL,
        params={"get": "B01003_001E", "for": "zip code tabulation area:*"},
        timeout=30,
    )
    r2.raise_for_status()
    rows = r2.json()          # [[pop, zcta], ...]
    header = rows[0]          # ["B01003_001E", "zip code tabulation area"]
    pop_col = header.index("B01003_001E")
    zip_col = header.index("zip code tabulation area")

    density: dict[str, float] = {}
    for row in rows[1:]:
        zcta = row[zip_col].strip()
        try:
            pop = float(row[pop_col])
        except (ValueError, TypeError):
            continue
        sqmi = area.get(zcta)
        if sqmi and sqmi > 0 and pop >= 0:
            density[zcta] = round(pop / sqmi, 1)

    print(f"  Computed density for {len(density)} ZIP codes")
    return density


# ── 2. INTERSTATE PROXIMITY ───────────────────────────────────────────────────

def fetch_interstate_geometry() -> MultiLineString:
    """Downloads MA motorway geometries from Overpass and returns a Shapely MultiLineString."""
    print("  Fetching MA interstate geometries from OpenStreetMap…")
    query = (
        '[out:json][timeout:90];'
        'way["highway"="motorway"](41.2,-73.6,43.0,-69.9);'
        'out geom qt;'
    )
    r = requests.post(OVERPASS_URL, data={"data": query}, headers=OVERPASS_HEADERS, timeout=100)
    r.raise_for_status()
    elements = r.json().get("elements", [])

    # Filter to actual interstates by ref tag
    lines = []
    for el in elements:
        ref = el.get("tags", {}).get("ref", "")
        # Keep if any token in the ref matches an interstate ref
        tokens = {tok.strip() for tok in ref.replace(";", "|").split("|")}
        if tokens & INTERSTATE_REFS:
            coords = [(pt["lon"], pt["lat"]) for pt in el.get("geometry", [])]
            if len(coords) >= 2:
                lines.append(coords)

    print(f"  {len(lines)} interstate segments loaded")
    return MultiLineString(lines)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(max(0.0, a)))


def dist_to_interstates(lat: float, lon: float, mls: MultiLineString) -> float:
    """Returns distance in miles to the nearest point on any MA interstate."""
    pt = Point(lon, lat)
    near = nearest_points(pt, mls)[1]
    return haversine_miles(lat, lon, near.y, near.x)


# ── 3. EXPANDED NAME PATTERNS ─────────────────────────────────────────────────

STRONG_INDIE_NAMES = [
    "PACKAGE STORE", "PACKAGE", "LIQUOR", "WINE & SPIRITS", "WINE AND SPIRITS",
    "SPIRITS", "SMOKE SHOP", "SMOKE", "CIGAR", "TOBACCO", "VAPE",
    "VARIETY", "VARIETY STORE", "SPA", "NAIL",
    "NEWS", "NEWSSTAND", "NEWSAGENT",
    "DELI", "SANDWICH", "SUB SHOP",
    "PIZZA", "BAKERY", "CAFE", "CAFÉ", "COFFEE",
    "GENERAL STORE", "COUNTRY STORE", "CORNER STORE", "CORNER MARKET",
    "FOOD MART", "MINI MART", "MINIMART",
    "MARKET", "GROCERY", "SUPERETTE", "BODEGA",
    "LAUNDRY", "LAUNDROMAT", "DRY CLEAN",
    "PHARMACY", "DRUG STORE",
    "PET STORE", "HARDWARE",
    "FLORIST", "GIFT SHOP",
    "BARBER", "BEAUTY", "SALON",
    "CONVENIENCE",
]

CHAIN_KEYWORDS = [
    "7-ELEVEN", "7 ELEVEN", "CUMBERLAND FARMS", "CUMBERLAND",
    "MOBIL", "SHELL", "BP ", "SUNOCO", "GULF ", "GETTY", "CITGO",
    "EXXON", "SPEEDWAY", "CIRCLE K", "IRVING OIL", "IRVING",
    "WALGREENS", "CVS", "SHAWS", "SHAW'S", "STOP & SHOP",
    "MARKET BASKET", "BIG Y", "PRICE RITE", "PRICE CHOPPER",
    "HANNAFORD", "STAR MARKET", "TARGET", "WALMART", "WAL-MART",
    "COSTCO", "BJ'S ", "BJS ", "DOLLAR GENERAL", "DOLLAR TREE",
    "FAMILY DOLLAR", "RITE AID", "DUNKIN", "MCDONALD", "SUBWAY",
    "NOURIA", "GLOBAL FUEL", "GULF ENERGY", "PRIDE",
]

GAS_KEYWORDS = ["GAS", "FUEL", "PETROLEUM", "SERVICE STATION", "AUTO"]


def classify_name(name: str) -> dict:
    n = name.upper()
    is_chain       = any(kw in n for kw in CHAIN_KEYWORDS)
    is_gas         = any(kw in n for kw in GAS_KEYWORDS) and not is_chain
    indie_strength = sum(1 for kw in STRONG_INDIE_NAMES if kw in n)
    return {
        "is_chain":       is_chain,
        "is_gas":         is_gas,
        "indie_strength": indie_strength,
    }


# ── 4. GOOGLE PLACES ENRICHMENT ───────────────────────────────────────────────

async def gmaps_enrich_one(
    client: httpx.AsyncClient,
    row: dict,
    api_key: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Enriches one retailer with Google Places data. Returns extra fields."""
    name    = row.get("name", "")
    address = row.get("address", "")
    city    = row.get("city", "")
    query   = f"{name} {address} {city} MA"

    async with semaphore:
        try:
            # Step 1: find the place
            r1 = await client.get(
                GMAPS_FIND_URL,
                params={
                    "input": query,
                    "inputtype": "textquery",
                    "fields": "place_id",
                    "locationbias": "circle:80000@42.1,-71.8",
                    "key": api_key,
                },
                timeout=10,
            )
            d1 = r1.json()
            candidates = d1.get("candidates", [])
            if not candidates:
                return {}

            place_id = candidates[0]["place_id"]

            # Step 2: get details
            r2 = await client.get(
                GMAPS_DETAILS_URL,
                params={
                    "place_id": place_id,
                    "fields": "user_ratings_total,rating,opening_hours,types",
                    "key": api_key,
                },
                timeout=10,
            )
            result = r2.json().get("result", {})

            review_count = result.get("user_ratings_total", -1)
            types        = result.get("types", [])
            hours        = result.get("opening_hours", {})
            periods      = hours.get("periods", [])

            # 24-hour: a period with open.time "0000" and no close
            is_24h = any(
                p.get("open", {}).get("time") == "0000" and "close" not in p
                for p in periods
            )
            # closes early (before 21:00 = 9pm) on weekdays
            closes_early = False
            for p in periods:
                close_time = p.get("close", {}).get("time", "")
                if close_time and int(close_time) < 2100:
                    closes_early = True
                    break

            has_lottery_type = "liquor_store" not in types and any(
                t in ("convenience_store", "gas_station") for t in types
            )

            return {
                "review_count":     review_count,
                "gmaps_types":      "|".join(types),
                "is_24h":           is_24h,
                "closes_early":     closes_early,
                "has_lottery_type": has_lottery_type,
            }
        except Exception:
            return {}


async def gmaps_enrich_all(
    retailers: list[dict], api_key: str, workers: int = 5
) -> list[dict]:
    semaphore = asyncio.Semaphore(workers)
    async with httpx.AsyncClient() as client:
        tasks = [gmaps_enrich_one(client, r, api_key, semaphore) for r in retailers]
        results = []
        total = len(tasks)
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            results.append(await coro)
            if i % 50 == 0 or i == total:
                print(f"    Google Places: {i}/{total}", end="\r")
    print()
    return results


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in",      dest="input",   default="ma_retailers.csv")
    parser.add_argument("--out",     dest="output",  default="ma_retailers_enriched.csv")
    parser.add_argument("--gmaps",   dest="gmaps_key", default="")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    in_path  = Path(args.input)
    out_path = Path(args.output)

    if not in_path.exists():
        print(f"ERROR: {in_path} not found. Run retailer_scraper.py first.")
        sys.exit(1)

    with open(in_path, newline="", encoding="utf-8") as f:
        retailers = list(csv.DictReader(f))
    print(f"Loaded {len(retailers)} retailers from {in_path}")

    # ── Layer 1: ZIP density ──────────────────────────────────────────────
    print("\n[1/3] ZIP population density...")
    density_map = fetch_zip_density()
    for r in retailers:
        z = r.get("zipCode", "").strip()[:5]
        r["pop_density"] = density_map.get(z, "")

    covered = sum(1 for r in retailers if r.get("pop_density") != "")
    print(f"  Density matched: {covered}/{len(retailers)} retailers")

    # ── Layer 2: Interstate proximity ─────────────────────────────────────
    print("\n[2/3] Interstate proximity...")
    interstate_mls = fetch_interstate_geometry()
    for i, r in enumerate(retailers):
        try:
            lat = float(r.get("latitude") or 0)
            lon = float(r.get("longitude") or 0)
            if lat and lon:
                r["interstate_dist_mi"] = round(dist_to_interstates(lat, lon, interstate_mls), 2)
            else:
                r["interstate_dist_mi"] = ""
        except Exception:
            r["interstate_dist_mi"] = ""
        if (i + 1) % 500 == 0 or (i + 1) == len(retailers):
            print(f"  {i+1}/{len(retailers)} distances computed", end="\r")
    print()
    covered = sum(1 for r in retailers if r.get("interstate_dist_mi") != "")
    print(f"  Distances computed: {covered}/{len(retailers)} retailers")

    # ── Layer 3: Expanded name classification ─────────────────────────────
    print("\n[3/3] Name pattern classification...")
    for r in retailers:
        c = classify_name(r.get("name", ""))
        r["is_chain_v2"]     = c["is_chain"]
        r["is_gas"]          = c["is_gas"]
        r["indie_strength"]  = c["indie_strength"]
    print("  Done")

    # ── Layer 4: Google Places (optional) ─────────────────────────────────
    if args.gmaps_key:
        print(f"\n[4/4] Google Places enrichment ({len(retailers)} stores, {args.workers} workers)...")
        gmaps_results = asyncio.run(gmaps_enrich_all(retailers, args.gmaps_key, args.workers))
        for r, extra in zip(retailers, gmaps_results):
            r.update(extra)
        print(f"  Enriched with Google Places data")
    else:
        print("\n[4/4] Google Places: skipped (pass --gmaps YOUR_KEY to enable)")
        for r in retailers:
            r.setdefault("review_count", "")
            r.setdefault("gmaps_types", "")
            r.setdefault("is_24h", "")
            r.setdefault("closes_early", "")
            r.setdefault("has_lottery_type", "")

    # ── Write output ──────────────────────────────────────────────────────
    all_fields = list(retailers[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        w.writerows(retailers)

    print(f"\nSaved -> {out_path}  ({len(retailers)} rows, {len(all_fields)} columns)")
    print("Run heatmap_generator.py --csv ma_retailers_enriched.csv to rebuild the map")


if __name__ == "__main__":
    main()
