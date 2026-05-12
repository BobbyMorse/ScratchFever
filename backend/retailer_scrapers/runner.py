"""
Retailer scraper runner — runs all state retailer scrapers and logs results.
Designed to be called monthly from the scheduler.
Each scraper runs in its own thread pool executor to avoid blocking the event loop.
"""
from __future__ import annotations
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from backend.database import get_pool

logger = logging.getLogger(__name__)

# Map of state_code -> scrape function (sync, returns list[dict])
# Each module exposes run(conn) which is async and handles upsert.
SCRAPERS = {
    "NY": "backend.retailer_scrapers.ny",
    "NJ": "backend.retailer_scrapers.nj",
    "GA": "backend.retailer_scrapers.ga",
    "CA": "backend.retailer_scrapers.ca",
}


async def run_all() -> list[dict]:
    """Run all state retailer scrapers sequentially and return summary."""
    results = []
    pool = get_pool()

    for state_code, module_path in SCRAPERS.items():
        logger.info("Retailer scrape starting: %s", state_code)
        try:
            mod = __import__(module_path, fromlist=["run"])
            async with pool.acquire() as conn:
                count = await mod.run(conn)
            logger.info("Retailer scrape complete: %s — %d upserted", state_code, count)
            results.append({"state": state_code, "count": count, "error": None})
        except Exception as e:
            logger.error("Retailer scrape failed: %s — %s", state_code, e)
            results.append({"state": state_code, "count": 0, "error": str(e)})

    return results


async def run_state(state_code: str) -> dict:
    """Run a single state's retailer scraper."""
    module_path = SCRAPERS.get(state_code.upper())
    if not module_path:
        return {"state": state_code, "count": 0, "error": "No scraper registered"}
    try:
        mod = __import__(module_path, fromlist=["run"])
        async with get_pool().acquire() as conn:
            count = await mod.run(conn)
        return {"state": state_code, "count": count, "error": None}
    except Exception as e:
        logger.error("Retailer scrape failed: %s — %s", state_code, e)
        return {"state": state_code, "count": 0, "error": str(e)}
