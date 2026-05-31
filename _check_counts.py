import os, asyncio, asyncpg
from dotenv import load_dotenv
load_dotenv()

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    for sc in ("TX", "CA", "CT"):
        n = await conn.fetchval("SELECT COUNT(*) FROM state_retailers WHERE state_code=$1", sc)
        ll = await conn.fetchval(
            "SELECT COUNT(*) FROM state_retailers WHERE state_code=$1 AND latitude IS NOT NULL", sc
        )
        print(f"{sc}: {n} total, {ll} with lat/lng")
    await conn.close()

asyncio.run(main())
