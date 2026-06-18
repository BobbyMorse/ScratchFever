"""
Backfill latitude/longitude on state_retailers rows that came in without geo.

Uses the US Census Bureau batch geocoder (free, no auth):
  POST https://geocoding.geo.census.gov/geocoder/locations/addressbatch
  multipart fields: addressFile=@csv, benchmark=Public_AR_Current
  CSV columns: id, address, city, state, zip
  Batch size: up to 10,000 rows per request

Usage:
    python backfill_retailer_geo.py              # all 0-geo states
    python backfill_retailer_geo.py VA DC        # specific states
"""
from __future__ import annotations
import asyncio
import csv
import io
import os
import sys
import logging

import asyncpg
import requests
from dotenv import load_dotenv

from backend.geo_validate import in_state_bbox, validate_latlon

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BENCHMARK  = "Public_AR_Current"
BATCH_SIZE = 5000  # Census says 10k max; smaller is more reliable


async def fetch_missing(conn, state_code: str) -> list[dict]:
    rows = await conn.fetch(
        """SELECT id, address, city, state_code AS state, zip_code AS zip
           FROM state_retailers
           WHERE state_code = $1 AND latitude IS NULL AND address IS NOT NULL""",
        state_code,
    )
    return [dict(r) for r in rows]


async def apply_fallback(conn, state_code: str, rows: list[dict], census_hits: dict[int, tuple[float, float]]) -> tuple[int, int]:
    """For rows Census couldn't place (or placed outside the state), run them
    through the ZIP/state-centroid fallback in validate_latlon. Returns
    (approx_applied, still_missing)."""
    approx_applied = still_missing = 0
    for r in rows:
        rid = r["id"]
        coords = census_hits.get(rid)
        if coords and in_state_bbox(state_code, coords[0], coords[1]):
            continue  # the in-bbox path already updated this row
        lat, lon, approx = validate_latlon(
            state_code,
            coords[0] if coords else None,
            coords[1] if coords else None,
            address=r.get("address"),
            city=r.get("city"),
            zip_code=r.get("zip"),
        )
        if lat is None:
            still_missing += 1
            continue
        await conn.execute(
            "UPDATE state_retailers SET latitude=$1, longitude=$2, geo_approximated=$3 WHERE id=$4",
            lat, lon, bool(approx), rid,
        )
        if approx:
            approx_applied += 1
    return approx_applied, still_missing


def build_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    for r in rows:
        w.writerow([r["id"], r["address"] or "", r["city"] or "", r["state"] or "", r["zip"] or ""])
    return buf.getvalue().encode("utf-8")


def geocode_batch(rows: list[dict]) -> dict[int, tuple[float, float]]:
    """Send one batch to Census; return {row_id: (lat, lng)} for matches."""
    csv_bytes = build_csv(rows)
    files = {"addressFile": ("addresses.csv", csv_bytes, "text/csv")}
    data  = {"benchmark": BENCHMARK}
    try:
        resp = requests.post(CENSUS_URL, files=files, data=data, timeout=600)
    except Exception as e:
        logger.warning("Census batch request failed: %s", e)
        return {}
    if resp.status_code != 200:
        logger.warning("Census HTTP %d: %s", resp.status_code, resp.text[:200])
        return {}

    out: dict[int, tuple[float, float]] = {}
    reader = csv.reader(io.StringIO(resp.text))
    for row in reader:
        # Census response columns:
        # 0:id 1:input 2:match_status 3:match_type 4:matched_address 5:lat,lng 6:tigerline_id 7:side
        if len(row) < 6:
            continue
        if row[2] != "Match":
            continue
        latlng = row[5]
        if not latlng or "," not in latlng:
            continue
        try:
            row_id = int(row[0])
            lng_s, lat_s = latlng.split(",", 1)
            out[row_id] = (float(lat_s), float(lng_s))
        except (ValueError, IndexError):
            continue
    return out


async def apply_updates(conn, updates: dict[int, tuple[float, float]]) -> int:
    if not updates:
        return 0
    args = [(lat, lng, rid) for rid, (lat, lng) in updates.items()]
    await conn.executemany(
        "UPDATE state_retailers SET latitude = $1, longitude = $2 WHERE id = $3",
        args,
    )
    return len(args)


async def process_state(conn, state_code: str) -> None:
    rows = await fetch_missing(conn, state_code)
    logger.info("[%s] %d rows missing geo", state_code, len(rows))
    if not rows:
        return
    total_matched = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        logger.info("[%s] batch %d-%d (%d rows) → Census …", state_code, i, i + len(batch), len(batch))
        result = await asyncio.to_thread(geocode_batch, batch)
        applied = await apply_updates(conn, result)
        total_matched += applied
        logger.info("[%s]   matched %d / %d → updated %d", state_code, len(result), len(batch), applied)

    final_geo = await conn.fetchval(
        "SELECT count(*) FROM state_retailers WHERE state_code=$1 AND latitude IS NOT NULL",
        state_code,
    )
    final_total = await conn.fetchval(
        "SELECT count(*) FROM state_retailers WHERE state_code=$1", state_code,
    )
    pct = (final_geo * 100 / final_total) if final_total else 0
    logger.info(
        "[%s] done — matched %d this run; %d / %d geocoded (%.0f%%)",
        state_code, total_matched, final_geo, final_total, pct,
    )


async def main(states: list[str]) -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        if not states:
            rows = await conn.fetch(
                """SELECT state_code FROM state_retailers
                   WHERE latitude IS NULL
                   GROUP BY state_code ORDER BY state_code"""
            )
            states = [r["state_code"] for r in rows]
            logger.info("Auto-detected 0-geo states: %s", ", ".join(states))
        for sc in states:
            await process_state(conn, sc)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main([s.upper() for s in sys.argv[1:]]))
