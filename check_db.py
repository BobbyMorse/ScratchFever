import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from backend.database import init_db, get_pool

async def check():
    await init_db()
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT state_code, success, games_scraped, error_msg, ran_at "
            "FROM scrape_log WHERE state_code IN ('FL', 'GA') ORDER BY ran_at DESC LIMIT 10"
        )
        print("=== FL/GA scrape log ===")
        for r in rows:
            print(dict(r))

        counts = await conn.fetch(
            "SELECT state_code, COUNT(*) as cnt FROM games WHERE is_active=TRUE GROUP BY state_code ORDER BY state_code"
        )
        print("\n=== Active games per state ===")
        for r in counts:
            print(r["state_code"], r["cnt"])

asyncio.run(check())
