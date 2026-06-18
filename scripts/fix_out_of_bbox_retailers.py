"""
One-off patch: find retailer rows whose stored lat/lon falls outside their
claimed state's bbox, re-geocode via Census, and update only those rows.

Backstop for source-feed errors (e.g. MA Lottery published Nouria #1294 in
Marlborough, MA at coords actually in southern NH). The importers now guard
against this going forward via backend.geo_validate.validate_latlon; this
script cleans up rows that landed before the guard existed.

Usage:
    python scripts/fix_out_of_bbox_retailers.py             # dry-run report
    python scripts/fix_out_of_bbox_retailers.py --apply     # write changes
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow `python scripts/...` invocation to import backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from dotenv import load_dotenv

from backend.geo_validate import (
    STATE_BBOX,
    in_state_bbox,
    validate_latlon,
)

load_dotenv()

PER_STATE_TABLES = [
    ("MA", "ma_retailers"),
    ("AZ", "az_retailers"),
    ("FL", "fl_retailers"),
    ("GA", "ga_retailers"),
    ("NY", "ny_retailers"),
    ("RI", "ri_retailers"),
]


async def scan_table(conn, state: str, table: str) -> list[dict]:
    bb = STATE_BBOX.get(state)
    if not bb:
        return []
    min_lat, max_lat, min_lon, max_lon = bb
    rows = await conn.fetch(
        f"""SELECT id, name, address, city, zip_code, latitude, longitude
            FROM {table}
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
              AND (latitude  NOT BETWEEN $1 AND $2
                OR longitude NOT BETWEEN $3 AND $4)""",
        min_lat, max_lat, min_lon, max_lon,
    )
    return [dict(r) for r in rows]


async def scan_state_retailers(conn) -> list[dict]:
    """state_retailers is multi-state; scan once and filter per-row."""
    rows = await conn.fetch(
        """SELECT id, state_code, name, address, city, zip_code, latitude, longitude
           FROM state_retailers
           WHERE latitude IS NOT NULL AND longitude IS NOT NULL"""
    )
    bad = []
    for r in rows:
        if not in_state_bbox(r["state_code"], r["latitude"], r["longitude"]):
            bad.append(dict(r))
    return bad


async def fix_row(conn, table: str, state: str, row: dict, apply: bool) -> str:
    new_lat, new_lon = validate_latlon(
        state,
        row["latitude"],
        row["longitude"],
        address=row.get("address"),
        city=row.get("city"),
        zip_code=row.get("zip_code"),
    )
    old = (row["latitude"], row["longitude"])
    if new_lat is None:
        verdict = "NULL  (no good Census match)"
    elif (new_lat, new_lon) == old:
        verdict = "no change"
    else:
        verdict = f"-> ({new_lat:.5f}, {new_lon:.5f})"

    if apply and (new_lat, new_lon) != old:
        if table == "state_retailers":
            await conn.execute(
                "UPDATE state_retailers SET latitude=$1, longitude=$2 WHERE id=$3",
                new_lat, new_lon, row["id"],
            )
        else:
            await conn.execute(
                f"UPDATE {table} SET latitude=$1, longitude=$2 WHERE id::text=$3",
                new_lat, new_lon, str(row["id"]),
            )
    return verdict


async def main(apply: bool):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        total_bad = total_fixed = total_nulled = 0
        for state, table in PER_STATE_TABLES:
            try:
                bad = await scan_table(conn, state, table)
            except asyncpg.UndefinedTableError:
                print(f"[{state}] {table}: table missing — skipping")
                continue
            if not bad:
                print(f"[{state}] {table}: clean")
                continue
            print(f"[{state}] {table}: {len(bad)} out-of-bbox row(s)")
            for r in bad:
                verdict = await fix_row(conn, table, state, r, apply)
                print(f"   id={r['id']} {r.get('name')!r} @ {r.get('city')}, {r.get('zip_code')}  "
                      f"feed=({r['latitude']:.5f}, {r['longitude']:.5f})  {verdict}")
                total_bad += 1
                if "NULL" in verdict:
                    total_nulled += 1
                elif "->" in verdict:
                    total_fixed += 1

        # state_retailers (multi-state)
        try:
            sr_bad = await scan_state_retailers(conn)
        except asyncpg.UndefinedTableError:
            sr_bad = []
        if sr_bad:
            print(f"[state_retailers] {len(sr_bad)} out-of-bbox row(s)")
            for r in sr_bad:
                verdict = await fix_row(conn, "state_retailers", r["state_code"], r, apply)
                print(f"   id={r['id']} state={r['state_code']} {r.get('name')!r} @ {r.get('city')}, {r.get('zip_code')}  "
                      f"feed=({r['latitude']:.5f}, {r['longitude']:.5f})  {verdict}")
                total_bad += 1
                if "NULL" in verdict:
                    total_nulled += 1
                elif "->" in verdict:
                    total_fixed += 1
        else:
            print("[state_retailers] clean")

        verb = "applied" if apply else "would apply"
        print(f"\nSummary: {total_bad} bad row(s); {verb} {total_fixed} re-geocode(s), "
              f"{total_nulled} NULL-out(s).")
        if not apply and total_bad:
            print("Re-run with --apply to write changes.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write changes (default: dry-run)")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
