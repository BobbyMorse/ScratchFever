"""
Standalone scraper worker — runs the APScheduler + all scraper jobs without
attaching the full FastAPI app. Intended to be deployed as a separate Railway
service sharing the API's DATABASE_URL, freeing the API process from the
event-loop starvation and DB-pool contention that comes with running heavy
scrapers in-process.

Usage:
    python -m backend.scraper_worker

Required env vars (same as the API):
    DATABASE_URL — Postgres connection string
    PLAYWRIGHT_BROWSERS_PATH — optional, defaults to /app/.playwright
    PORT — provided by Railway; minimal HTTP server binds it for healthchecks

A tiny FastAPI /health is served alongside the scheduler so Railway's
healthcheck (configured in railway.toml at the repo level) succeeds. The
worker does NOT serve any other routes — only /health.

On the API service, set DISABLE_SCHEDULER=1 once this worker is healthy so
the API stops also running the same jobs.
"""
from __future__ import annotations
import asyncio
import logging
import os

from dotenv import load_dotenv
load_dotenv()

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from backend.database import init_db
from backend.scheduler_jobs import register_jobs, ensure_playwright_browsers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STARTUP_DELAY_SEC = 30  # short delay so DB pool warms up before the first cycle

healthcheck_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@healthcheck_app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok", "service": "scraper_worker"}


async def main() -> None:
    logger.info("scraper_worker: starting")
    await init_db()
    logger.info("scraper_worker: DB initialized")

    scheduler = AsyncIOScheduler()
    kick_games_now = register_jobs(scheduler)
    scheduler.start()
    logger.info("scraper_worker: scheduler started (winners hourly, retailer-freshness daily)")

    # Minimal HTTP server so Railway's repo-level healthcheck on /health passes.
    port = int(os.environ.get("PORT", "8080"))
    server = uvicorn.Server(uvicorn.Config(
        healthcheck_app, host="0.0.0.0", port=port,
        log_level="warning", access_log=False,
    ))
    asyncio.create_task(server.serve())
    logger.info("scraper_worker: healthcheck HTTP server bound on port %d", port)

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

    # Block forever; APScheduler + uvicorn both run on this event loop.
    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        logger.info("scraper_worker: shutting down")
        scheduler.shutdown(wait=False)
        server.should_exit = True


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("scraper_worker: interrupted")
