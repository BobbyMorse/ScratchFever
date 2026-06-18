"""
Import ga_retailers.csv into the Supabase ga_retailers table.

Creates the table if it doesn't exist, then upserts all rows.

Usage:
    python import_ga_retailers.py
    python import_ga_retailers.py --csv ga_retailers.csv
"""
import asyncio
import csv
import os
import argparse
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from backend.geo_validate import validate_latlon

load_dotenv()

CSV_PATH = Path("ga_retailers.csv")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ga_retailers (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    address     TEXT,
    city        TEXT,
    state       TEXT DEFAULT 'GA',
    zip_code    TEXT,
    phone       TEXT,
    latitude    DOUBLE PRECISION,
    longitude   DOUBLE PRECISION,
    is_active   BOOLEAN DEFAULT TRUE,
    last_seen_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def _float(v: str) -> float | None:
    try:
        return float(v) if v not in ("", None) else None
    except (ValueError, TypeError):
        return None


async def main(csv_path: Path):
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], statement_cache_size=0)

    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE)
        print("Table ga_retailers ready.")

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} rows from {csv_path}")

    inserted = updated = skipped = 0

    async with pool.acquire() as conn:
        for row in rows:
            rid = row.get("id", "").strip()
            if not rid:
                skipped += 1
                continue
            lat, lon, geo_approx = validate_latlon(
                "GA",
                _float(row.get("latitude")),
                _float(row.get("longitude")),
                address=row.get("address"),
                city=row.get("city"),
                zip_code=row.get("zipCode"),
            )
            result = await conn.execute("""
                INSERT INTO ga_retailers (id, name, address, city, state, zip_code, phone, latitude, longitude, geo_approximated, is_active, last_seen_at)
                VALUES ($1,$2,$3,$4,'GA',$5,$6,$7,$8,$9,TRUE,NOW())
                ON CONFLICT (id) DO UPDATE SET
                    name             = EXCLUDED.name,
                    address          = EXCLUDED.address,
                    city             = EXCLUDED.city,
                    zip_code         = EXCLUDED.zip_code,
                    phone            = EXCLUDED.phone,
                    latitude         = EXCLUDED.latitude,
                    longitude        = EXCLUDED.longitude,
                    geo_approximated = EXCLUDED.geo_approximated,
                    is_active        = TRUE,
                    last_seen_at     = NOW()
            """,
                rid,
                row.get("name", "").strip(),
                row.get("address", "").strip(),
                row.get("city", "").strip(),
                row.get("zipCode", "").strip(),
                row.get("phone", "").strip(),
                lat,
                lon,
                geo_approx,
            )
            if result == "INSERT 0 1":
                inserted += 1
            else:
                updated += 1

    count = await pool.fetchval("SELECT COUNT(*) FROM ga_retailers")
    print(f"Done — inserted: {inserted}, updated: {updated}, skipped: {skipped}")
    print(f"Total rows in ga_retailers: {count}")
    await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()
    path = Path(args.csv) if args.csv else CSV_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run fetch_ga_retailers.py first")
    asyncio.run(main(path))
