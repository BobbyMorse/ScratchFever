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


async def scan_table(conn, state: str, table: str, include_nulls: bool) -> list[dict]:
    """Out-of-bbox rows, plus (when include_nulls=True) rows whose lat/lon got
    NULLed by an earlier guard pass and have enough address info to retry."""
    bb = STATE_BBOX.get(state)
    if not bb:
        return []
    min_lat, max_lat, min_lon, max_lon = bb
    bad = await conn.fetch(
        f"""SELECT id, name, address, city, zip_code, latitude, longitude
            FROM {table}
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
              AND (latitude  NOT BETWEEN $1 AND $2
                OR longitude NOT BETWEEN $3 AND $4)""",
        min_lat, max_lat, min_lon, max_lon,
    )
    rows = [dict(r) for r in bad]
    if include_nulls:
        null_rows = await conn.fetch(
            f"""SELECT id, name, address, city, zip_code, latitude, longitude
                FROM {table}
                WHERE latitude IS NULL AND zip_code IS NOT NULL"""
        )
        rows.extend(dict(r) for r in null_rows)
    return rows


async def scan_state_retailers(conn, rescue_null_states: list[str] | None = None) -> list[dict]:
    """state_retailers is multi-state. Always scan for out-of-bbox rows; only
    scan latitude IS NULL for states explicitly opted-in (rescue_null_states),
    to avoid stomping on backfill_retailer_geo.py's domain for states whose
    geo is genuinely still pending."""
    rows = await conn.fetch(
        """SELECT id, state_code, name, address, city, zip_code, latitude, longitude
           FROM state_retailers
           WHERE latitude IS NOT NULL AND longitude IS NOT NULL"""
    )
    bad = [dict(r) for r in rows if not in_state_bbox(r["state_code"], r["latitude"], r["longitude"])]

    if rescue_null_states:
        null_rows = await conn.fetch(
            """SELECT id, state_code, name, address, city, zip_code, latitude, longitude
               FROM state_retailers
               WHERE latitude IS NULL AND zip_code IS NOT NULL AND state_code = ANY($1)""",
            [s.upper() for s in rescue_null_states],
        )
        bad.extend(dict(r) for r in null_rows)
    return bad


async def fix_row(conn, table: str, state: str, row: dict, apply: bool) -> str:
    new_lat, new_lon, approx = validate_latlon(
        state,
        row["latitude"],
        row["longitude"],
        address=row.get("address"),
        city=row.get("city"),
        zip_code=row.get("zip_code"),
    )
    old = (row["latitude"], row["longitude"])
    if new_lat is None:
        verdict = "NULL  (no centroid available)"
    elif (new_lat, new_lon) == old:
        verdict = "no change"
    elif approx:
        verdict = f"~> ({new_lat:.5f}, {new_lon:.5f}) [approx]"
    else:
        verdict = f"-> ({new_lat:.5f}, {new_lon:.5f})"

    if apply:
        if table == "state_retailers":
            await conn.execute(
                "UPDATE state_retailers SET latitude=$1, longitude=$2, geo_approximated=$3 WHERE id=$4",
                new_lat, new_lon, bool(approx), row["id"],
            )
        else:
            await conn.execute(
                f"UPDATE {table} SET latitude=$1, longitude=$2, geo_approximated=$3 WHERE id::text=$4",
                new_lat, new_lon, bool(approx), str(row["id"]),
            )
    return verdict


async def main(apply: bool, include_nulls: bool, rescue_null_states: list[str]):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        total_bad = total_fixed = total_approx = total_nulled = 0
        for state, table in PER_STATE_TABLES:
            try:
                bad = await scan_table(conn, state, table, include_nulls)
            except asyncpg.UndefinedTableError:
                print(f"[{state}] {table}: table missing — skipping")
                continue
            if not bad:
                print(f"[{state}] {table}: clean")
                continue
            print(f"[{state}] {table}: {len(bad)} row(s) needing review")
            for r in bad:
                verdict = await fix_row(conn, table, state, r, apply)
                feed = ("(NULL,NULL)" if r['latitude'] is None
                        else f"({r['latitude']:.5f}, {r['longitude']:.5f})")
                print(f"   id={r['id']} {r.get('name')!r} @ {r.get('city')}, {r.get('zip_code')}  "
                      f"feed={feed}  {verdict}")
                total_bad += 1
                if "NULL" in verdict:
                    total_nulled += 1
                elif "[approx]" in verdict:
                    total_approx += 1
                elif "->" in verdict:
                    total_fixed += 1

        try:
            sr_bad = await scan_state_retailers(conn, rescue_null_states)
        except asyncpg.UndefinedTableError:
            sr_bad = []
        if sr_bad:
            print(f"[state_retailers] {len(sr_bad)} row(s) needing review")
            for r in sr_bad:
                verdict = await fix_row(conn, "state_retailers", r["state_code"], r, apply)
                feed = ("(NULL,NULL)" if r['latitude'] is None
                        else f"({r['latitude']:.5f}, {r['longitude']:.5f})")
                print(f"   id={r['id']} state={r['state_code']} {r.get('name')!r} @ {r.get('city')}, {r.get('zip_code')}  "
                      f"feed={feed}  {verdict}")
                total_bad += 1
                if "NULL" in verdict:
                    total_nulled += 1
                elif "[approx]" in verdict:
                    total_approx += 1
                elif "->" in verdict:
                    total_fixed += 1
        else:
            print("[state_retailers] clean")

        verb = "applied" if apply else "would apply"
        print(f"\nSummary: {total_bad} row(s); {verb} {total_fixed} census-fix, "
              f"{total_approx} approx-fallback, {total_nulled} unfixable.")
        if not apply and total_bad:
            print("Re-run with --apply to write changes.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write changes (default: dry-run)")
    parser.add_argument("--include-nulls", action="store_true",
                        help="Also rescue per-state-table rows whose lat/lon is NULL (small populations)")
    parser.add_argument("--rescue-null-states", nargs="*", default=[],
                        help="state codes in state_retailers to also rescue from NULL (skip otherwise: backfill_retailer_geo.py owns those)")
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.include_nulls, args.rescue_null_states))
