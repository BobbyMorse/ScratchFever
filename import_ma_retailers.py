"""
One-time import: load ma_retailers_enriched.csv (or ma_retailers.csv) into
the ma_retailers Postgres table.

Usage:
    python import_ma_retailers.py
    python import_ma_retailers.py --csv ma_retailers.csv   # base CSV fallback
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

ENRICHED = Path("ma_retailers_enriched.csv")
BASE     = Path("ma_retailers.csv")


def _bool(val: str) -> bool:
    return str(val).lower() in ("true", "1", "yes")


def _float(val: str) -> float | None:
    try:
        return float(val) if val not in ("", None) else None
    except (ValueError, TypeError):
        return None


def _int(val: str) -> int | None:
    try:
        return int(val) if val not in ("", None) else None
    except (ValueError, TypeError):
        return None


def load_csv(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


async def main(csv_path: Path):
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], statement_cache_size=0)

    rows = load_csv(csv_path)
    print(f"Loaded {len(rows)} rows from {csv_path}")

    inserted = updated = skipped = 0

    async with pool.acquire() as conn:
        for row in rows:
            rid = _int(row.get("id"))
            if not rid:
                skipped += 1
                continue

            lat, lon, geo_approx = validate_latlon(
                "MA",
                _float(row.get("latitude")),
                _float(row.get("longitude")),
                address=row.get("address"),
                city=row.get("city"),
                zip_code=row.get("zipCode"),
            )

            result = await conn.execute("""
                INSERT INTO ma_retailers (
                    id, retailer_id, name, address, address2, city, zip_code, phone,
                    latitude, longitude, geo_approximated, games,
                    self_service, sells_draw_game,
                    keno_sold, keno_monitor, keno_type,
                    wol_sold, wol_monitor, wol_type, ada_compliant,
                    pop_density, interstate_dist_mi, is_chain, is_gas,
                    indie_strength, review_count, is_24h, closes_early,
                    is_active, last_seen_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                    $13,$14,$15,$16,$17,$18,$19,$20,$21,
                    $22,$23,$24,$25,$26,$27,$28,$29,
                    TRUE, NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    name               = EXCLUDED.name,
                    address            = EXCLUDED.address,
                    address2           = EXCLUDED.address2,
                    city               = EXCLUDED.city,
                    zip_code           = EXCLUDED.zip_code,
                    phone              = EXCLUDED.phone,
                    latitude           = EXCLUDED.latitude,
                    longitude          = EXCLUDED.longitude,
                    games              = EXCLUDED.games,
                    self_service       = EXCLUDED.self_service,
                    sells_draw_game    = EXCLUDED.sells_draw_game,
                    keno_sold          = EXCLUDED.keno_sold,
                    keno_monitor       = EXCLUDED.keno_monitor,
                    keno_type          = EXCLUDED.keno_type,
                    wol_sold           = EXCLUDED.wol_sold,
                    wol_monitor        = EXCLUDED.wol_monitor,
                    wol_type           = EXCLUDED.wol_type,
                    ada_compliant      = EXCLUDED.ada_compliant,
                    pop_density        = COALESCE(EXCLUDED.pop_density, ma_retailers.pop_density),
                    interstate_dist_mi = COALESCE(EXCLUDED.interstate_dist_mi, ma_retailers.interstate_dist_mi),
                    is_chain           = COALESCE(EXCLUDED.is_chain, ma_retailers.is_chain),
                    is_gas             = COALESCE(EXCLUDED.is_gas, ma_retailers.is_gas),
                    indie_strength     = COALESCE(EXCLUDED.indie_strength, ma_retailers.indie_strength),
                    review_count       = COALESCE(EXCLUDED.review_count, ma_retailers.review_count),
                    is_24h             = COALESCE(EXCLUDED.is_24h, ma_retailers.is_24h),
                    closes_early       = COALESCE(EXCLUDED.closes_early, ma_retailers.closes_early),
                    is_active          = TRUE,
                    last_seen_at       = NOW()
            """,
                rid,
                _int(row.get("retailerId")),
                row.get("name", "").strip(),
                row.get("address", "").strip(),
                row.get("address2", "").strip(),
                row.get("city", "").strip(),
                row.get("zipCode", "").strip(),
                row.get("phone", "").strip(),
                lat,
                lon,
                row.get("games", ""),
                _bool(row.get("selfService", "")),
                _bool(row.get("sellsDrawGame", "")),
                _bool(row.get("kenoSoldAtLocation", "")),
                _bool(row.get("kenoMonitor", "")),
                row.get("kenoType", ""),
                _bool(row.get("wolSoldAtLocation", "")),
                _bool(row.get("wolMonitor", "")),
                row.get("wolType", ""),
                row.get("adaCompliant", ""),
                _float(row.get("pop_density")),
                _float(row.get("interstate_dist_mi")),
                _bool(row.get("is_chain_v2", row.get("is_chain", ""))),
                _bool(row.get("is_gas", "")),
                _int(row.get("indie_strength")) or 0,
                _int(row.get("review_count")),
                _bool(row.get("is_24h", "")),
                _bool(row.get("closes_early", "")),
            )
            if result == "INSERT 0 1":
                inserted += 1
            else:
                updated += 1

    count = await pool.fetchval("SELECT COUNT(*) FROM ma_retailers")
    print(f"Done — inserted: {inserted}, updated: {updated}, skipped: {skipped}")
    print(f"Total rows in ma_retailers: {count}")
    await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=None, help="CSV path (default: enriched if exists)")
    args = parser.parse_args()

    if args.csv:
        path = Path(args.csv)
    elif ENRICHED.exists():
        path = ENRICHED
    elif BASE.exists():
        path = BASE
    else:
        raise FileNotFoundError("No ma_retailers CSV found")

    asyncio.run(main(path))
