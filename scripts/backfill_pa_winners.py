"""
Concurrent PA winners backfill.

PA's JsWinners endpoint requires county+year+month, so a 10-year backfill is
~8,000 GETs. Sequential requests are unbearably slow, so this script runs
controlled-concurrency fetches per (year, month) and upserts results per month
so partial progress is preserved.

Usage:
    python scripts/backfill_pa_winners.py [years]   # default: 10
"""
from __future__ import annotations
import asyncio
import datetime as dt
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from dotenv import load_dotenv
load_dotenv()

from backend.database import init_db, get_pool, upsert_reported_wins  # noqa: E402
from backend.scraper.winners.pennsylvania import (  # noqa: E402
    PA_COUNTIES, MONTH_NAMES, URL, PennsylvaniaWinnersScraper,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_pa")

CONCURRENCY = 16
TIMEOUT = aiohttp.ClientTimeout(total=30)
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, */*",
}


async def fetch_county_month(session, county: str, year: int, month: str, sem) -> list[dict]:
    async with sem:
        for attempt in range(1, 5):
            try:
                async with session.get(URL, params={
                    "county": county, "year": year, "month": month,
                }) as resp:
                    if resp.status == 200:
                        text = (await resp.text()).strip()
                        if not text or text == "[]":
                            return []
                        try:
                            import json
                            return json.loads(text)
                        except json.JSONDecodeError:
                            return []
                    if resp.status in (429, 500, 502, 503, 504):
                        await asyncio.sleep(min(8.0, 2.0 ** attempt))
                        continue
                    return []
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await asyncio.sleep(min(8.0, 2.0 ** attempt))
        return []


async def main():
    years_back = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    today = dt.date.today()
    start_year = today.year - years_back
    end_year = today.year

    await init_db()
    sem = asyncio.Semaphore(CONCURRENCY)
    normalizer = PennsylvaniaWinnersScraper()._normalize

    total_saved = 0
    async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
        for year in range(end_year, start_year - 1, -1):
            for m_idx, month_name in enumerate(MONTH_NAMES, start=1):
                if year == today.year and m_idx > today.month:
                    continue
                tasks = [fetch_county_month(session, c, year, month_name, sem)
                         for c in PA_COUNTIES]
                results = await asyncio.gather(*tasks)
                raw_items: list[dict] = []
                for r in results:
                    raw_items.extend(r)
                if not raw_items:
                    logger.info("  PA %s %d: 0 wins", month_name, year)
                    continue
                seen = set()
                normalized: list[dict] = []
                for w in raw_items:
                    n = normalizer(w, year, m_idx)
                    if not n:
                        continue
                    if n["source_id"] in seen:
                        continue
                    seen.add(n["source_id"])
                    normalized.append(n)
                if not normalized:
                    continue
                async with get_pool().acquire() as conn:
                    saved = await upsert_reported_wins(conn, "PA", normalized)
                total_saved += saved
                logger.info("  PA %s %d: %d wins -> %d new (cum %d)",
                            month_name, year, len(normalized), saved, total_saved)

    logger.info("DONE - total PA wins saved: %d", total_saved)


if __name__ == "__main__":
    asyncio.run(main())
