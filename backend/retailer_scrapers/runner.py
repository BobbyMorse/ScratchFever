"""
Retailer scraper runner.

run_stale(max_age_days=30) — checks retailer_scrape_log for each state;
    runs scrapers for any state not updated within max_age_days.
    Call on startup and on a daily interval so the scrape happens automatically
    the first time the server comes up after a 30-day gap.

run_all() — forces a full scrape of every state regardless of staleness.
"""
from __future__ import annotations
import asyncio
import importlib
import logging
from datetime import datetime, timezone

from backend.database import get_pool

logger = logging.getLogger(__name__)

# Ordered list of states to scrape.
# Each entry maps to backend/retailer_scrapers/<state_lower>.py exposing run(conn).
SCRAPERS: list[str] = [
    "MA", "AZ", "NY", "NJ", "GA", "CA", "NH", "CT", "IL", "DC", "VT", "VA",
    # Added scrapers
    "ME", "OR", "CO", "SC", "WA", "TX",
]


async def _log_scrape(conn, state_code: str, count: int) -> None:
    await conn.execute("""
        INSERT INTO retailer_scrape_log (state_code, last_scraped_at, retailers_count)
        VALUES ($1, NOW(), $2)
        ON CONFLICT (state_code) DO UPDATE SET
            last_scraped_at = NOW(),
            retailers_count = EXCLUDED.retailers_count
    """, state_code, count)


async def _get_last_scraped(conn) -> dict[str, datetime]:
    rows = await conn.fetch("SELECT state_code, last_scraped_at FROM retailer_scrape_log")
    return {r["state_code"]: r["last_scraped_at"] for r in rows}


async def _run_state(state_code: str) -> dict:
    module = importlib.import_module(f"backend.retailer_scrapers.{state_code.lower()}")
    pool = get_pool()
    async with pool.acquire() as conn:
        count = await module.run(conn)
    async with pool.acquire() as conn:
        await _log_scrape(conn, state_code, count)
    return {"state": state_code, "count": count, "error": None}


async def run_stale(max_age_days: int = 30) -> list[dict]:
    """Scrape states whose retailer data is older than max_age_days (or never scraped)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        last_scraped = await _get_last_scraped(conn)

    now = datetime.now(timezone.utc)
    stale = []
    for state in SCRAPERS:
        last = last_scraped.get(state)
        if last is None:
            age_days = None
            stale.append(state)
        else:
            last_aware = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            age_days = (now - last_aware).days
            if age_days >= max_age_days:
                stale.append(state)
        logger.info(
            "Retailer freshness %s: %s days old%s",
            state,
            age_days if age_days is not None else "never",
            " → STALE" if state in stale else "",
        )

    if not stale:
        logger.info("All retailer states are fresh, skipping scrape")
        return []

    logger.info("Starting retailer scrape for stale states: %s", stale)
    results = []
    for state in stale:
        try:
            result = await _run_state(state)
            logger.info("Retailer scrape done: %s — %d retailers", state, result["count"])
        except Exception as e:
            logger.error("Retailer scrape failed: %s — %s", state, e)
            result = {"state": state, "count": 0, "error": str(e)}
        results.append(result)

    return results


async def run_all() -> list[dict]:
    """Force-scrape every state regardless of staleness."""
    results = []
    for state in SCRAPERS:
        try:
            result = await _run_state(state)
            logger.info("Retailer scrape done: %s — %d retailers", state, result["count"])
        except Exception as e:
            logger.error("Retailer scrape failed: %s — %s", state, e)
            result = {"state": state, "count": 0, "error": str(e)}
        results.append(result)
    return results
