"""
Standalone scraper worker — runs the APScheduler + all scraper jobs without
attaching FastAPI. Intended to be deployed as a separate Railway service
sharing the API's DATABASE_URL, freeing the API process from the event-loop
starvation and DB-pool contention that comes with running heavy scrapers
in-process.

Usage:
    python -m backend.scraper_worker

Required env vars (same as the API):
    DATABASE_URL — Postgres connection string
    PLAYWRIGHT_BROWSERS_PATH — optional, defaults to /app/.playwright

On the API service, set DISABLE_SCHEDULER=1 once this worker is healthy so
the API stops also running the same jobs.
"""
from __future__ import annotations
import asyncio
import logging

from dotenv import load_dotenv
load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.database import init_db
from backend.scheduler_jobs import register_jobs, ensure_playwright_browsers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STARTUP_DELAY_SEC = 30  # short delay so DB pool warms up before the first cycle


async def main() -> None:
    logger.info("scraper_worker: starting")
    await init_db()
    logger.info("scraper_worker: DB initialized")

    scheduler = AsyncIOScheduler()
    kick_games_now = register_jobs(scheduler)
    scheduler.start()
    logger.info("scraper_worker: scheduler started (winners hourly, retailer-freshness daily)")

    async def _delayed_first_cycle():
        await asyncio.sleep(STARTUP_DELAY_SEC)
        try:
            await asyncio.to_thread(ensure_playwright_browsers)
        except Exception as e:
            logger.warning("Playwright self-heal failed: %s", e)
        logger.info("scraper_worker: kicking off first games cycle")
        try:
            await kick_games_now()
        except Exception:
            logger.exception("first games cycle failed")

    asyncio.create_task(_delayed_first_cycle())

    # Block forever; APScheduler runs on the same event loop.
    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        logger.info("scraper_worker: shutting down scheduler")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("scraper_worker: interrupted")
