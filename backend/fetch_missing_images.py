"""
Backfill missing image_url values for active games.

For each state with < 100% image coverage, runs the state's scraper and
updates image_url only for rows that currently have NULL.
"""
import asyncio
import logging
import os
import sys
import time

import asyncpg
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Map state_code -> scraper class (only states with working scrapers)
def _build_scraper_map():
    from backend.scraper.runner import ALL_SCRAPERS
    return {cls.state_code.upper(): cls for cls in ALL_SCRAPERS}


async def get_states_with_missing_images(conn) -> list[tuple[str, str, int]]:
    """Returns [(state_code, state_name, missing_count)] sorted by missing_count desc."""
    rows = await conn.fetch("""
        SELECT state_code, state_name,
               COUNT(*) FILTER (WHERE image_url IS NULL) AS missing,
               COUNT(*) AS total
        FROM games
        WHERE is_active = TRUE
        GROUP BY state_code, state_name
        HAVING COUNT(*) FILTER (WHERE image_url IS NULL) > 0
        ORDER BY missing DESC
    """)
    return [(r["state_code"], r["state_name"], r["missing"]) for r in rows]


async def backfill_state(conn, state_code: str, scraper_cls) -> int:
    scraper = scraper_cls()
    logger.info("%s: running scraper to collect image URLs…", state_code)
    games, error = await asyncio.to_thread(scraper.safe_scrape)
    if error or not games:
        logger.warning("%s: scraper failed — %s", state_code, error)
        return 0

    # Build game_id -> image_url map from scraper results
    img_map = {
        g["game_id"]: g["image_url"]
        for g in games
        if g.get("image_url")
    }
    logger.info("%s: scraper returned %d games, %d with images", state_code, len(games), len(img_map))

    if not img_map:
        return 0

    updated = 0
    for game_id, image_url in img_map.items():
        result = await conn.execute(
            """UPDATE games SET image_url = $1
               WHERE state_code = $2 AND game_id = $3 AND image_url IS NULL""",
            image_url, state_code, game_id,
        )
        if result == "UPDATE 1":
            updated += 1

    return updated


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL not set")

    conn = await asyncpg.connect(db_url, statement_cache_size=0)
    try:
        states = await get_states_with_missing_images(conn)
        if not states:
            logger.info("All states have 100%% image coverage — nothing to do.")
            return

        scraper_map = _build_scraper_map()
        logger.info("States with missing images: %s",
                    ", ".join(f"{code}({n})" for code, _, n in states))

        total_updated = 0
        for i, (code, name, missing) in enumerate(states):
            if code not in scraper_map:
                logger.warning("%s: no scraper available, skipping", code)
                continue

            logger.info("--- %s (%s): %d missing images ---", name, code, missing)
            updated = await backfill_state(conn, code, scraper_map[code])
            logger.info("%s: filled in %d image URLs", code, updated)
            total_updated += updated

            if i < len(states) - 1:
                logger.info("Sleeping 5s before next state…")
                await asyncio.sleep(5)

        logger.info("Done. Total image URLs filled in: %d", total_updated)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
