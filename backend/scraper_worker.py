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
import datetime
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
load_dotenv()

# Shrink new-thread stack from the glibc default (8MB) to 512KB. With ~46 game
# scrapers + 31 winners scrapers + DB pool + uvicorn workers all calling
# asyncio.to_thread, the default sizing repeatedly exhausted the 512MB Railway
# dyno and surfaced as `RuntimeError: can't start new thread`. Must be set
# BEFORE the first thread is spawned to take effect for that thread.
threading.stack_size(512 * 1024)

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from backend.database import init_db
from backend.scheduler_jobs import register_jobs, ensure_playwright_browsers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STARTUP_DELAY_SEC = 30  # short delay so DB pool warms up before the first cycle

# Hard ceiling on worker uptime. Playwright + curl_cffi leak OS threads (DNS
# resolver pools, Chromium child process pipes, getaddrinfo workers) faster
# than they release them. Past ~8h the process can no longer pthread_create
# and the Playwright lane wedges — every PW scraper then fails with "can't
# start new thread" until something restarts the container. Exiting every
# WORKER_MAX_UPTIME_SEC pre-empts that: we exit with a non-zero code so
# Railway's ON_FAILURE restart policy (the default) spawns a fresh process.
# Exit-0 would NOT trigger a restart — that bug stranded the worker for 7+
# hours on 2026-06-19 (winners API stopped getting ingested mid-day).
WORKER_MAX_UPTIME_SEC = int(os.environ.get("WORKER_MAX_UPTIME_SEC", "21600"))  # 6h

# If the winners job hasn't completed successfully in this long, something
# wedged the scheduler (job ran past max_instances, event loop blocked, etc.)
# Exit non-zero so Railway restarts us — a 7h silent stall is unacceptable.
# Winners runs hourly with a 30-min misfire grace, so 2.5h is the slack we
# give before declaring the scheduler dead.
WORKER_STALE_THRESHOLD_SEC = int(os.environ.get("WORKER_STALE_THRESHOLD_SEC", "9000"))  # 2.5h

healthcheck_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_worker_status = {"phase": "booting", "error": None}
# Mutated in-place by scheduler_jobs.register_jobs(heartbeat=_heartbeat).
# Keys: games_last_success, winners_last_success, retailer_last_success (UTC).
_heartbeat: dict[str, datetime.datetime] = {}
_worker_started_at = datetime.datetime.utcnow()


def _isoformat(d: datetime.datetime | None) -> str | None:
    return d.isoformat() + "Z" if d else None


@healthcheck_app.get("/health", include_in_schema=False)
async def health() -> dict:
    # Always return 200 once the process is up — Railway just needs to see a
    # response. Detailed phase + heartbeat ages are surfaced for debugging
    # via the dashboard and the API-side staleness probe.
    now = datetime.datetime.utcnow()
    def _age_sec(d):
        return (now - d).total_seconds() if d else None
    return {
        "status": "ok",
        "service": "scraper_worker",
        "started_at": _isoformat(_worker_started_at),
        "uptime_sec": int((now - _worker_started_at).total_seconds()),
        "heartbeat": {
            "games_last_success":    _isoformat(_heartbeat.get("games_last_success")),
            "winners_last_success":  _isoformat(_heartbeat.get("winners_last_success")),
            "retailer_last_success": _isoformat(_heartbeat.get("retailer_last_success")),
            "games_age_sec":    _age_sec(_heartbeat.get("games_last_success")),
            "winners_age_sec":  _age_sec(_heartbeat.get("winners_last_success")),
            "retailer_age_sec": _age_sec(_heartbeat.get("retailer_last_success")),
        },
        **_worker_status,
    }


async def _bootstrap() -> None:
    """Heavy init runs in the background so the /health endpoint is reachable
    immediately — Railway's healthcheck would otherwise time out during the
    init_db DDL phase or the first DB connection retry."""
    try:
        _worker_status["phase"] = "init_db"
        await init_db()
        logger.info("scraper_worker: DB initialized")

        _worker_status["phase"] = "scheduler_start"
        scheduler = AsyncIOScheduler()
        kick_games_now = register_jobs(scheduler, heartbeat=_heartbeat)
        scheduler.start()
        logger.info("scraper_worker: scheduler started (winners hourly, retailer-freshness daily)")
        _worker_status["phase"] = "scheduled"

        await asyncio.sleep(STARTUP_DELAY_SEC)
        try:
            await asyncio.to_thread(ensure_playwright_browsers)
        except Exception as e:
            logger.warning("Playwright self-heal failed: %s", e)

        logger.info("scraper_worker: kicking off first games cycle")
        _worker_status["phase"] = "first_cycle"
        try:
            await kick_games_now()
        except Exception:
            logger.exception("first games cycle failed")
        _worker_status["phase"] = "running"
    except Exception as e:
        logger.exception("scraper_worker bootstrap failed")
        _worker_status["phase"] = "bootstrap_failed"
        _worker_status["error"] = str(e)


async def main() -> None:
    logger.info("scraper_worker: starting")

    # Bound the default ThreadPoolExecutor. asyncio.to_thread otherwise lets the
    # pool grow to min(32, CPU+4) on demand, and on Railway that — combined with
    # threads created inside scrapers (Playwright, curl-cffi DNS) — has been
    # enough to hit pthread_create EAGAIN. 8 workers is plenty: games cycle
    # caps at HTTP_CONCURRENCY=4 + PW_CONCURRENCY=1 = 5 concurrent, and the
    # winners cycle runs sequentially.
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=8))

    # Bind the healthcheck HTTP server FIRST so Railway's /health probe
    # succeeds while the DB init + scheduler boot happens in the background.
    port = int(os.environ.get("PORT", "8080"))
    server = uvicorn.Server(uvicorn.Config(
        healthcheck_app, host="0.0.0.0", port=port,
        log_level="warning", access_log=False,
    ))
    server_task = asyncio.create_task(server.serve())
    logger.info("scraper_worker: healthcheck HTTP server bound on port %d", port)

    bootstrap_task = asyncio.create_task(_bootstrap())
    uptime_task = asyncio.create_task(_uptime_watchdog())

    try:
        await server_task
    finally:
        logger.info("scraper_worker: shutting down")
        bootstrap_task.cancel()
        uptime_task.cancel()


async def _uptime_watchdog() -> None:
    """Self-restart after WORKER_MAX_UPTIME_SEC. See module-level comment."""
    started = time.monotonic()
    while True:
        await asyncio.sleep(60)
        if time.monotonic() - started >= WORKER_MAX_UPTIME_SEC:
            logger.warning(
                "scraper_worker: max uptime %ds reached — exiting so Railway "
                "restarts with a clean thread/process tree",
                WORKER_MAX_UPTIME_SEC,
            )
            # _exit (not sys.exit) so we don't wait on any wedged Playwright
            # subprocess pipes during shutdown — those are exactly what we're
            # trying to escape.
            os._exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("scraper_worker: interrupted")
