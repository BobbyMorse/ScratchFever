"""
One-shot geocode for existing reported_wins rows that were inserted before
the corresponding state_retailers table was populated.

For each state, matches retailer_name + retailer_city in reported_wins against
state_retailers (using the same normalization as upsert_reported_wins) and
fills in retailer_lat / retailer_lng.

Usage:
    python scripts/geocode_existing_wins.py [STATE_CODE ...]
    # default: all states present in reported_wins
"""
from __future__ import annotations
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from backend.database import (  # noqa: E402
    init_db, get_pool, _norm_retailer_name, _load_retailer_geo_index,
)
from backend.scraper.winners.city_geocoder import geocode_city  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("geocode_wins")

BATCH = 1000


async def backfill_state(conn, state_code: str) -> tuple[int, int]:
    exact, substr = await _load_retailer_geo_index(conn, state_code)
    logger.info("%s: %d exact-match retailers loaded", state_code, len(exact))

    rows = await conn.fetch(
        """SELECT id, retailer_name, retailer_city, winner_city
           FROM reported_wins
           WHERE state_code = $1 AND retailer_lat IS NULL""",
        state_code,
    )
    logger.info("%s: %d wins need geocoding", state_code, len(rows))

    updates: list[tuple[float, float, int]] = []
    miss = 0
    for r in rows:
        hit = None
        # 1. Exact retailer-name + city match against state_retailers
        norm_name = _norm_retailer_name(r["retailer_name"] or "")
        norm_city = (r["retailer_city"] or "").lower().strip()
        if norm_name and norm_city and exact:
            hit = exact.get((norm_name, norm_city))
            if not hit:
                candidates = substr.get(norm_city) or []
                for cand_name, cand_lat, cand_lng in candidates:
                    if norm_name in cand_name or cand_name in norm_name:
                        hit = (cand_lat, cand_lng)
                        break
        # 2. City centroid fallback: retailer_city OR winner_city
        if not hit:
            fallback_city = r["retailer_city"] or r["winner_city"]
            if fallback_city:
                hit = geocode_city(fallback_city, state_code)
        if not hit:
            miss += 1
            continue
        updates.append((float(hit[0]), float(hit[1]), r["id"]))

    if not updates:
        return 0, miss

    sql = """UPDATE reported_wins
             SET retailer_lat = $1, retailer_lng = $2
             WHERE id = $3"""
    saved = 0
    for i in range(0, len(updates), BATCH):
        chunk = updates[i:i + BATCH]
        await conn.executemany(sql, chunk)
        saved += len(chunk)
        if (saved % 10000) < BATCH:
            logger.info("  %s: %d / %d updated", state_code, saved, len(updates))
    return saved, miss


async def main():
    await init_db()
    if len(sys.argv) > 1:
        states = [s.upper() for s in sys.argv[1:]]
    else:
        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT state_code FROM reported_wins WHERE retailer_lat IS NULL"
            )
            states = sorted(r["state_code"] for r in rows)
        if not states:
            logger.info("No wins missing geo — nothing to do")
            return

    logger.info("Geocoding states: %s", states)
    for state in states:
        async with get_pool().acquire() as conn:
            saved, miss = await backfill_state(conn, state)
        logger.info("=== %s: %d filled, %d unmatched ===", state, saved, miss)


if __name__ == "__main__":
    asyncio.run(main())
