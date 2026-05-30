"""
One-off loader: scrape Maine retailers and upsert them into state_retailers.

Runs backend/retailer_scrapers/me.py's scrape_me() and persists results via the
shared upsert_retailers helper (state_code='ME'). Also logs the run into
retailer_scrape_log so the daily freshness checker won't re-scrape immediately.

Usage:
    python import_me_retailers.py
"""
from __future__ import annotations
import asyncio
import logging
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    from backend.retailer_scrapers.me import scrape_me
    from backend.retailer_scrapers.base import upsert_retailers

    retailers = await asyncio.to_thread(scrape_me)
    if not retailers:
        logger.error("ME: no retailers scraped — aborting load")
        return

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], statement_cache_size=0)
    async with pool.acquire() as conn:
        count = await upsert_retailers(conn, "ME", retailers)
        await conn.execute("""
            INSERT INTO retailer_scrape_log (state_code, last_scraped_at, retailers_count)
            VALUES ('ME', NOW(), $1)
            ON CONFLICT (state_code) DO UPDATE SET
                last_scraped_at = NOW(),
                retailers_count = EXCLUDED.retailers_count
        """, count)
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM state_retailers WHERE state_code='ME'"
        )
    await pool.close()

    logger.info("Upserted %d ME retailers (state_retailers total now %d)", count, total)


if __name__ == "__main__":
    asyncio.run(main())
