"""
MA Lottery Retailer Scraper
============================
Tiles Massachusetts with a 1-mile grid, hits the MA Lottery
/api/v2/retailers/nearby endpoint from each grid point, deduplicates
by retailer ID, and writes ma_retailers.csv.

Usage:
  python retailer_scraper.py               # full MA grid (default)
  python retailer_scraper.py --spacing 2   # coarser 2-mile grid (faster)
  python retailer_scraper.py --workers 30  # more concurrent workers
"""

import asyncio
import csv
import json
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

import httpx

# ── MA bounding box ──────────────────────────────────────────────────────────
MA_LAT_MIN, MA_LAT_MAX = 41.23, 42.89
MA_LON_MIN, MA_LON_MAX = -73.53, -69.93

# Miles → degrees (approximate at MA latitude ~42°)
MILES_PER_DEG_LAT = 69.0
MILES_PER_DEG_LON = 51.0

API_BASE = "https://mslc-prod-herokuapp-com.global.ssl.fastly.net"
ENDPOINT = f"{API_BASE}/api/v2/retailers/nearby"

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.masslottery.com",
    "Referer": "https://www.masslottery.com/tools/location-finder",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
}

CSV_FIELDS = [
    "id", "retailerId", "name", "address", "address2", "city",
    "state", "zipCode", "phone", "latitude", "longitude",
    "games", "selfService", "sellsDrawGame",
    "kenoSoldAtLocation", "kenoMonitor", "kenoType",
    "wolSoldAtLocation", "wolMonitor", "wolType",
    "adaCompliant",
]


def build_grid(spacing_miles: float) -> list[tuple[float, float]]:
    lat_step = spacing_miles / MILES_PER_DEG_LAT
    lon_step = spacing_miles / MILES_PER_DEG_LON
    points = []
    lat = MA_LAT_MIN
    while lat <= MA_LAT_MAX:
        lon = MA_LON_MIN
        while lon <= MA_LON_MAX:
            points.append((round(lat, 6), round(lon, 6)))
            lon += lon_step
        lat += lat_step
    return points


async def fetch_point(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    semaphore: asyncio.Semaphore,
    retries: int = 3,
) -> list[dict]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "userLatitude": lat,
        "userLongitude": lon,
    }
    async with semaphore:
        for attempt in range(retries):
            try:
                r = await client.get(ENDPOINT, params=params, headers=HEADERS, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        return data
                    return []
                if r.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt < retries - 1:
                    await asyncio.sleep(1)
    return []


def flatten(rec: dict) -> dict:
    games = rec.get("games") or []
    return {
        "id":                 rec.get("id"),
        "retailerId":         rec.get("retailerId"),
        "name":               rec.get("name", "").strip(),
        "address":            rec.get("address", "").strip(),
        "address2":           rec.get("address2", "").strip(),
        "city":               rec.get("city", "").strip(),
        "state":              rec.get("state", "MA"),
        "zipCode":            rec.get("zipCode", "").strip(),
        "phone":              rec.get("phone", "").strip(),
        "latitude":           rec.get("latitude"),
        "longitude":          rec.get("longitude"),
        "games":              "|".join(games),
        "selfService":        rec.get("selfService", False),
        "sellsDrawGame":      rec.get("sellsDrawGame", False),
        "kenoSoldAtLocation": rec.get("kenoSoldAtLocation", False),
        "kenoMonitor":        rec.get("kenoMonitor", False),
        "kenoType":           rec.get("kenoType", ""),
        "wolSoldAtLocation":  rec.get("wolSoldAtLocation", False),
        "wolMonitor":         rec.get("wolMonitor", False),
        "wolType":            rec.get("wolType", ""),
        "adaCompliant":       rec.get("adaCompliant", ""),
    }


async def scrape(spacing_miles: float = 1.0, workers: int = 20) -> list[dict]:
    grid = build_grid(spacing_miles)
    total = len(grid)
    print(f"Grid: {total} points at {spacing_miles}-mile spacing")
    print(f"Workers: {workers} concurrent")

    semaphore = asyncio.Semaphore(workers)
    seen_ids: set[int] = set()
    retailers: list[dict] = []
    completed = 0
    t0 = time.time()

    async with httpx.AsyncClient() as client:
        tasks = [
            fetch_point(client, lat, lon, semaphore)
            for lat, lon in grid
        ]

        for coro in asyncio.as_completed(tasks):
            results = await coro
            for rec in results:
                rid = rec.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    retailers.append(flatten(rec))

            completed += 1
            if completed % 100 == 0 or completed == total:
                elapsed = time.time() - t0
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (total - completed) / rate if rate > 0 else 0
                print(
                    f"  {completed}/{total} grid points  "
                    f"| {len(retailers)} unique retailers  "
                    f"| {rate:.0f} pts/s  "
                    f"| ETA {eta:.0f}s",
                    end="\r",
                )

    print()
    return retailers


def write_csv(retailers: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(retailers)
    print(f"Saved {len(retailers)} retailers -> {path}")


def load_previous(path: Path) -> dict[str, dict]:
    """Load existing CSV into a dict keyed by retailer id (as str)."""
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {row["id"]: row for row in csv.DictReader(f) if row.get("id")}


def diff_retailers(
    previous: dict[str, dict],
    current: list[dict],
    changelog_path: Path,
) -> None:
    current_map = {str(r["id"]): r for r in current if r.get("id") is not None}
    prev_ids = set(previous.keys())
    curr_ids = set(current_map.keys())

    new_ids = curr_ids - prev_ids
    gone_ids = prev_ids - curr_ids

    if not new_ids and not gone_ids:
        print("Location diff: no changes detected.")
        return

    timestamp = datetime.now().isoformat(timespec="seconds")
    entries = []

    if new_ids:
        print(f"\nNew locations ({len(new_ids)}):")
        for rid in sorted(new_ids):
            r = current_map[rid]
            line = f"  + [{rid}] {r['name']}  {r['address']}, {r['city']}"
            print(line)
            entries.append({"ts": timestamp, "change": "added", "id": rid,
                            "name": r["name"], "address": r["address"],
                            "city": r["city"], "state": r.get("state", "")})

    if gone_ids:
        print(f"\nDiscontinued locations ({len(gone_ids)}):")
        for rid in sorted(gone_ids):
            r = previous[rid]
            line = f"  - [{rid}] {r['name']}  {r['address']}, {r['city']}"
            print(line)
            entries.append({"ts": timestamp, "change": "removed", "id": rid,
                            "name": r["name"], "address": r["address"],
                            "city": r["city"], "state": r.get("state", "")})

    # Append to changelog JSONL (one JSON object per line)
    with open(changelog_path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    print(f"\nChangelog updated -> {changelog_path}  ({len(entries)} new entries)")


async def upsert_to_db(conn, retailers: list[dict]) -> tuple[int, int]:
    """Upsert lottery-provided fields only; never clobbers enrichment columns."""
    inserted = updated = 0
    for r in retailers:
        rid = r.get("id")
        if not rid:
            continue

        def _b(v) -> bool:
            return str(v).lower() in ("true", "1", "yes") if v else False

        result = await conn.execute("""
            INSERT INTO ma_retailers (
                id, retailer_id, name, address, address2, city, zip_code, phone,
                latitude, longitude, games,
                self_service, sells_draw_game,
                keno_sold, keno_monitor, keno_type,
                wol_sold, wol_monitor, wol_type, ada_compliant,
                is_active, last_seen_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,
                $12,$13,$14,$15,$16,$17,$18,$19,$20,
                TRUE, NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                name            = EXCLUDED.name,
                address         = EXCLUDED.address,
                address2        = EXCLUDED.address2,
                city            = EXCLUDED.city,
                zip_code        = EXCLUDED.zip_code,
                phone           = EXCLUDED.phone,
                latitude        = EXCLUDED.latitude,
                longitude       = EXCLUDED.longitude,
                games           = EXCLUDED.games,
                self_service    = EXCLUDED.self_service,
                sells_draw_game = EXCLUDED.sells_draw_game,
                keno_sold       = EXCLUDED.keno_sold,
                keno_monitor    = EXCLUDED.keno_monitor,
                keno_type       = EXCLUDED.keno_type,
                wol_sold        = EXCLUDED.wol_sold,
                wol_monitor     = EXCLUDED.wol_monitor,
                wol_type        = EXCLUDED.wol_type,
                ada_compliant   = EXCLUDED.ada_compliant,
                is_active       = TRUE,
                last_seen_at    = NOW()
        """,
            int(rid),
            int(r["retailerId"]) if r.get("retailerId") else None,
            str(r.get("name", "")).strip(),
            str(r.get("address", "")).strip(),
            str(r.get("address2", "")).strip(),
            str(r.get("city", "")).strip(),
            str(r.get("zipCode", "")).strip(),
            str(r.get("phone", "")).strip(),
            float(r["latitude"]) if r.get("latitude") is not None else None,
            float(r["longitude"]) if r.get("longitude") is not None else None,
            str(r.get("games", "")),
            _b(r.get("selfService")),
            _b(r.get("sellsDrawGame")),
            _b(r.get("kenoSoldAtLocation")),
            _b(r.get("kenoMonitor")),
            str(r.get("kenoType", "")),
            _b(r.get("wolSoldAtLocation")),
            _b(r.get("wolMonitor")),
            str(r.get("wolType", "")),
            str(r.get("adaCompliant", "")),
        )
        if result == "INSERT 0 1":
            inserted += 1
        else:
            updated += 1

    # Mark retailers not seen this run as inactive
    seen_ids = [int(r["id"]) for r in retailers if r.get("id")]
    if seen_ids:
        await conn.execute(
            "UPDATE ma_retailers SET is_active = FALSE WHERE id != ALL($1::int[])",
            seen_ids,
        )

    return inserted, updated


async def scrape_and_save_db(conn, spacing_miles: float = 1.0, workers: int = 20) -> dict:
    """Scrape MA lottery API and upsert results into the database. Returns summary."""
    retailers = await scrape(spacing_miles=spacing_miles, workers=workers)
    inserted, updated = await upsert_to_db(conn, retailers)
    return {"total": len(retailers), "inserted": inserted, "updated": updated}


async def main():
    parser = argparse.ArgumentParser(description="Scrape MA Lottery retailers to CSV")
    parser.add_argument("--spacing", type=float, default=1.0, help="Grid spacing in miles (default 1.0)")
    parser.add_argument("--workers", type=int, default=20, help="Concurrent HTTP workers (default 20)")
    parser.add_argument("--out", default="ma_retailers.csv", help="Output CSV path")
    parser.add_argument("--changelog", default="ma_retailers_changelog.jsonl",
                        help="Append-only JSONL log of added/removed locations (default ma_retailers_changelog.jsonl)")
    parser.add_argument("--no-diff", action="store_true", help="Skip location diff check")
    args = parser.parse_args()

    out_path = Path(args.out)
    changelog_path = Path(args.changelog)

    previous = {} if args.no_diff else load_previous(out_path)
    if previous:
        print(f"Loaded {len(previous)} previous retailers from {out_path} for diff")

    t0 = time.time()
    retailers = await scrape(spacing_miles=args.spacing, workers=args.workers)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.0f}s — {len(retailers)} unique MA retailers")

    if previous:
        diff_retailers(retailers, changelog_path)

    write_csv(retailers, out_path)


if __name__ == "__main__":
    asyncio.run(main())
