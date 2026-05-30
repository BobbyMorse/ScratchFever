"""
One-shot MI winners historical backfill, year-chunked so partial progress survives transient
503s. Each year's wins are upserted before fetching the next, so a mid-run failure only
loses the in-flight year.

Usage:
    python scripts/backfill_mi_winners.py [start_year] [end_year]
    # defaults: start_year=current-10, end_year=current
"""
from __future__ import annotations
import asyncio
import datetime as dt
import logging
import os
import sys
import time

import requests

from dotenv import load_dotenv
load_dotenv()

from backend.database import init_db, get_pool, upsert_reported_wins  # noqa: E402
from backend.scraper.winners.michigan import (  # noqa: E402
    API_URL, QUERY, PAGE_SIZE, MichiganWinnersScraper,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_mi")

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 8


def fetch_window(session: requests.Session, date_from: str, date_to: str) -> list[dict]:
    out: list[dict] = []
    start = 0
    total = None
    while True:
        payload = {
            "query": QUERY,
            "variables": {
                "min": 10000,
                "n": PAGE_SIZE,
                "i": start,
                "from": date_from,
                "to": date_to,
            },
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = session.post(API_URL, json=payload, timeout=60)
            except requests.RequestException as e:
                logger.warning("post error attempt %d: %s", attempt, e)
                resp = None
            if resp is not None and resp.status_code == 200:
                break
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"MI fetch failed after {MAX_RETRIES} attempts: "
                    f"status={resp.status_code if resp is not None else 'EXC'}"
                )
            backoff = min(60.0, 2.0 ** attempt)
            logger.warning("status=%s — sleeping %.1fs",
                           resp.status_code if resp is not None else "exc", backoff)
            time.sleep(backoff)
        data = resp.json()
        bw = (data.get("data") or {}).get("bigWinners") or {}
        page = bw.get("results") or []
        if total is None:
            total = bw.get("count") or 0
            logger.info("  window %s..%s — total=%d", date_from, date_to, total)
        if not page:
            break
        out.extend(page)
        start += PAGE_SIZE
        if start >= total:
            break
        # Polite pacing to avoid MI throttle
        time.sleep(0.15)
    return out


async def main():
    today = dt.date.today()
    end_year = int(sys.argv[2]) if len(sys.argv) > 2 else today.year
    start_year = int(sys.argv[1]) if len(sys.argv) > 1 else today.year - 10

    await init_db()
    session = requests.Session()
    session.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json, */*;q=0.5",
        "Content-Type": "application/json",
        "Referer": "https://www.michiganlottery.com/resources/big-winners",
        "Origin": "https://www.michiganlottery.com",
    })

    # Use the existing _normalize helper so output shape matches the live scraper.
    norm = MichiganWinnersScraper()._normalize

    total_saved = 0
    for year in range(end_year, start_year - 1, -1):
        date_from = f"{year}-01-01"
        date_to = f"{year}-12-31" if year != today.year else today.isoformat()
        logger.info("=== MI %s..%s ===", date_from, date_to)
        try:
            raw = fetch_window(session, date_from, date_to)
        except Exception as e:
            logger.error("year %d fetch failed: %s — moving on", year, e)
            continue
        wins = []
        seen = set()
        for w in raw:
            n = norm(w)
            if not n:
                continue
            if n["source_id"] in seen:
                continue
            seen.add(n["source_id"])
            wins.append(n)
        if not wins:
            logger.info("  year %d: 0 wins", year)
            continue
        async with get_pool().acquire() as conn:
            saved = await upsert_reported_wins(conn, "MI", wins)
        total_saved += saved
        logger.info("  year %d: %d wins → %d new rows (cumulative %d)",
                    year, len(wins), saved, total_saved)

    logger.info("DONE — total new MI rows: %d", total_saved)


if __name__ == "__main__":
    asyncio.run(main())
