"""Run scrapers for specified states (or all if no args)."""
import asyncio, sys
from dotenv import load_dotenv
load_dotenv()
from backend.database import init_db
from backend.scraper.runner import run_all

async def main():
    await init_db()
    state_filter = sys.argv[1].upper() if len(sys.argv) > 1 else None
    results = await run_all(state_filter=state_filter)
    print("\n=== Results ===")
    for r in results:
        status = "OK" if not r["error"] else f"ERROR: {r['error'][:80]}"
        print(f"  {r['state']}: {r['games']} games  [{status}]")

asyncio.run(main())
