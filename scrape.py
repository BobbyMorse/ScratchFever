"""
Run scrapers manually from the command line.
Usage:
  python scrape.py          # scrape all states
  python scrape.py TX       # scrape Texas only
  python scrape.py TX FL CA # scrape specific states
"""
import sys
import asyncio
import logging
from backend.database import init_db
from backend.scraper.runner import run_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main():
    await init_db()
    states = [s.upper() for s in sys.argv[1:]] if len(sys.argv) > 1 else [None]
    for state in states:
        print(f"\n{'='*50}")
        print(f"Scraping: {'ALL STATES' if not state else state}")
        print("=" * 50)
        results = await run_all(state_filter=state)
        for r in results:
            status = "OK" if not r["error"] else f"ERROR: {r['error'][:80]}"
            print(f"  {r['state']:2s}  {r['games']:4d} games  {status}")


if __name__ == "__main__":
    asyncio.run(main())
