"""
Orchestrates per-state recent-winners scrapers and persists to reported_wins.
Runs independently of the games scraper — winners feeds are usually faster
and lighter, so this can run on a tighter schedule.
"""
from __future__ import annotations
import asyncio
import logging

from backend.database import get_pool, upsert_reported_wins
from backend.scraper.winners.massachusetts import MassachusettsWinnersScraper
from backend.scraper.winners.michigan import MichiganWinnersScraper
from backend.scraper.winners.rhode_island import RhodeIslandWinnersScraper
from backend.scraper.winners.pennsylvania import PennsylvaniaWinnersScraper

logger = logging.getLogger(__name__)

ALL_WINNERS_SCRAPERS = [
    MassachusettsWinnersScraper,
    MichiganWinnersScraper,
    RhodeIslandWinnersScraper,
    PennsylvaniaWinnersScraper,
]

TIMEOUT_SEC = 180


async def run_one(scraper_cls, days: int = 14) -> dict:
    scraper = scraper_cls()
    code = scraper.state_code
    try:
        wins, error = await asyncio.wait_for(
            asyncio.to_thread(scraper.safe_scrape, days),
            timeout=TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        return {"state": code, "saved": 0, "error": f"timed out after {TIMEOUT_SEC}s"}
    if error:
        return {"state": code, "saved": 0, "error": error}
    saved = 0
    try:
        async with get_pool().acquire() as conn:
            saved = await upsert_reported_wins(conn, code, wins)
    except Exception as e:
        logger.exception("%s winners upsert failed", code)
        return {"state": code, "saved": 0, "error": f"db: {e}"}
    return {"state": code, "saved": saved, "error": None}


async def run_all(state_filter: str | None = None, days: int = 14) -> list[dict]:
    scrapers = ALL_WINNERS_SCRAPERS
    if state_filter:
        scrapers = [s for s in scrapers if s.state_code.upper() == state_filter.upper()]
    results = []
    for cls in scrapers:
        results.append(await run_one(cls, days=days))
    return results


def run_all_sync(state_filter: str | None = None, days: int = 14) -> list[dict]:
    return asyncio.run(run_all(state_filter, days))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import sys
    state = sys.argv[1] if len(sys.argv) > 1 else None
    print(run_all_sync(state))
