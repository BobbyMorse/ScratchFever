"""
One-shot cleanup for the MO reported_wins pollution.

The old missouri scraper iterated months and copied the same response from
the MO Lottery monthly winners page (which ignores y/m params) into each
month, producing N duplicate rows per real win with synthesized claim_dates.

This script:
  1) deletes all reported_wins rows for MO
  2) re-runs the fixed MO scraper to repopulate the current published month

After running this, /api/reported-wins/map will reflect only real MO data.

Usage:
    python scripts/cleanup_mo_winners.py
"""
from __future__ import annotations
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from backend.database import init_db, get_pool, upsert_reported_wins  # noqa: E402
from backend.scraper.winners.missouri import MissouriWinnersScraper  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cleanup_mo")


async def main():
    await init_db()
    async with get_pool().acquire() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM reported_wins WHERE state_code = 'MO'")
        deleted = await conn.execute("DELETE FROM reported_wins WHERE state_code = 'MO'")
        logger.info("Deleted MO reported_wins: before=%s, result=%s", before, deleted)

    scraper = MissouriWinnersScraper()
    wins, error = scraper.safe_scrape(days=14)
    if error:
        logger.error("MO re-scrape failed: %s", error)
        return
    logger.info("MO re-scrape: %d wins parsed", len(wins))

    async with get_pool().acquire() as conn:
        saved = await upsert_reported_wins(conn, "MO", wins)
        after = await conn.fetchval("SELECT COUNT(*) FROM reported_wins WHERE state_code = 'MO'")
    logger.info("Re-inserted: saved=%d, total MO rows now=%s", saved, after)


if __name__ == "__main__":
    asyncio.run(main())
