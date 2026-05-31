"""One-off: force-run a single state retailer scraper against prod DB."""
import asyncio, logging, os, sys
import asyncpg
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATE = sys.argv[1].upper()


async def main():
    import importlib
    mod = importlib.import_module(f"backend.retailer_scrapers.{STATE.lower()}")
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        count = await mod.run(conn)
        await conn.execute(
            """INSERT INTO retailer_scrape_log (state_code, last_scraped_at, retailers_count)
               VALUES ($1, NOW(), $2)
               ON CONFLICT (state_code) DO UPDATE SET
                 last_scraped_at = NOW(), retailers_count = EXCLUDED.retailers_count""",
            STATE, count,
        )
        print(f"DONE {STATE}: {count} retailers")
    finally:
        await conn.close()


asyncio.run(main())
