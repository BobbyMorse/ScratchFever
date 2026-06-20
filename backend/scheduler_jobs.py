"""
Scheduled-job functions, decoupled from FastAPI so they can run either in the
API process (current default) or in a standalone worker process — see
backend/scraper_worker.py and the Phase 3 cutover runbook.

`register_jobs(scheduler, status_dict=None)` wires the three recurring jobs
onto the given APScheduler. The status_dict, if provided, is mutated in place
so callers (e.g. the API) can read live state via the /api/scrape/status
endpoint. The worker process passes None and lets the DB scrape_log be the
authoritative status source.
"""
from __future__ import annotations
import asyncio
import datetime
import glob
import logging
import os
import subprocess
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 5-min pause between full games crawl cycles. Matches the prior in-process
# default so behavior is unchanged when DISABLE_SCHEDULER is unset.
SCRAPE_COOLDOWN_SEC = 300


# ── Pure job functions (no scheduler / status coupling) ────────────────────

async def run_games_scrape_cycle(on_state: Optional[Callable[[str, str], None]] = None) -> list[dict]:
    """One full pass across all configured state games scrapers."""
    from backend.scraper.runner import run_all
    from backend.database import clear_games_cache
    results = await run_all(on_state=on_state)
    clear_games_cache()
    return results


async def run_winners_scrape_cycle() -> list[dict]:
    """One pass across all configured winners feeds (19 states)."""
    from backend.scraper.winners.runner import run_all as run_winners
    return await run_winners(days=14)


async def run_retailer_freshness_cycle() -> list[dict]:
    """Daily check: scrape any retailer state whose data is >30d old."""
    from backend.retailer_scrapers.runner import run_stale
    return await run_stale(max_age_days=30)


def ensure_playwright_browsers() -> None:
    """Install Chromium if missing. Railway's build can drop the binary from
    the runtime image, breaking every Playwright scraper. Self-heal on boot."""
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/app/.playwright")
    pattern = os.path.join(browsers_path, "chromium_headless_shell-*",
                           "chrome-headless-shell-linux64", "chrome-headless-shell")
    if glob.glob(pattern):
        logger.info("Playwright headless-shell already installed at %s", browsers_path)
        return
    logger.warning("Playwright headless-shell missing — installing into %s", browsers_path)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": browsers_path}
    try:
        subprocess.run(
            ["python", "-m", "playwright", "install", "chromium", "chromium-headless-shell"],
            env=env, check=True, timeout=300,
        )
        logger.info("Playwright install complete")
    except Exception as e:
        logger.error("Playwright self-heal install failed: %s", e)


# ── Scheduler wiring ───────────────────────────────────────────────────────

def register_jobs(scheduler, status_dict: Optional[dict] = None,
                  on_winners_finish: Optional[Callable[[], None]] = None,
                  heartbeat: Optional[dict] = None) -> Callable:
    """Register the three recurring jobs on the given AsyncIOScheduler.

    Returns a coroutine function `kick_games_now()` the caller can `await`
    to start the first games cycle immediately (don't wait the cron interval).

    status_dict, if passed, is mutated with keys: running, current_state,
    last_results, last_run. The API process reads these for /api/scrape/status.

    heartbeat, if passed, is mutated on every successful job completion with
    keys: games_last_success, winners_last_success, retailer_last_success
    (all UTC datetimes). The scraper_worker uses this to (a) surface staleness
    on /health and (b) self-exit when the scheduler goes silent so Railway
    restarts the container.
    """
    def _stamp_heartbeat(key: str):
        if heartbeat is not None:
            heartbeat[key] = datetime.datetime.utcnow()

    async def _games_with_status():
        if status_dict and status_dict.get("running"):
            logger.info("Games scrape already running, skipping")
            return
        if status_dict is not None:
            status_dict["running"] = True
            status_dict["current_state"] = None
        try:
            logger.info("Starting games scrape cycle")
            def _on_state(code, name):
                if status_dict is not None:
                    status_dict["current_state"] = {"code": code, "name": name}
            results = await run_games_scrape_cycle(on_state=_on_state)
            if status_dict is not None:
                status_dict["last_results"] = results
                status_dict["last_run"] = datetime.datetime.utcnow().isoformat()
            _stamp_heartbeat("games_last_success")
            logger.info("Games scrape complete — next cycle in %ds", SCRAPE_COOLDOWN_SEC)
        except Exception as e:
            logger.error("Games scrape cycle failed: %s", e, exc_info=True)
        finally:
            if status_dict is not None:
                status_dict["running"] = False
            # Always re-schedule — even after a crash — so the scraper never dies.
            scheduler.add_job(
                _games_with_status, "date",
                run_date=datetime.datetime.utcnow() + datetime.timedelta(seconds=SCRAPE_COOLDOWN_SEC),
                id="scrape_next", replace_existing=True,
            )

    async def _winners_job():
        try:
            results = await run_winners_scrape_cycle()
            logger.info("winners scrape: %s", results)
            _stamp_heartbeat("winners_last_success")
            if on_winners_finish is not None:
                try:
                    on_winners_finish()
                except Exception:
                    logger.exception("on_winners_finish callback failed")
        except Exception:
            logger.exception("scheduled winners scrape failed")

    async def _retailer_job():
        try:
            results = await run_retailer_freshness_cycle()
            _stamp_heartbeat("retailer_last_success")
            if results:
                logger.info("Retailer scrape complete: %s", results)
        except Exception:
            logger.exception("retailer staleness check failed")

    # First retailer pass kicks off ~5 minutes after boot. Without an explicit
    # next_run_time APScheduler waits one full interval (24h) before its first
    # firing — combined with the scraper_worker's 6h max-uptime self-restart,
    # the daily job would never fire at all and stale states (>30d) would never
    # auto-refresh. misfire_grace_time generous because a full sweep of all
    # stale states can take many minutes if multiple are due at once.
    scheduler.add_job(
        _retailer_job, "interval", days=1, id="check_retailer_freshness",
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=300),
        max_instances=1, misfire_grace_time=3600, coalesce=True,
    )
    # First winners pass kicks off ~3 minutes after boot so newly-added state
    # scrapers get ingested on the very first deploy without waiting a full
    # interval. APScheduler's `interval` trigger otherwise waits one whole
    # interval before its first firing — meaning a fresh deploy could sit an
    # hour with no winners data.
    #
    # max_instances=1 + misfire_grace_time prevent both overlapping runs (if
    # one pass takes >1h) and silent skips (if the loop was paused briefly).
    scheduler.add_job(
        _winners_job, "interval", hours=1, id="scrape_winners",
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=180),
        max_instances=1, misfire_grace_time=1800, coalesce=True,
    )

    return _games_with_status
