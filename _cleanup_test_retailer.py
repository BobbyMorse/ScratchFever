"""Temporary verification + sweep by retailer_name."""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
import asyncpg

EMAIL  = "teststore@scratchfever.app"
RET_ID = "TEST-WINTHROP-001"


async def main():
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    try:
        # Verify
        for tbl in ["inventory_reports", "retailer_posts", "retailer_profiles"]:
            n = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl} WHERE retailer_id=$1", RET_ID)
            print(f"  {tbl} by retailer_id: {n}")
        n_name = await conn.fetchval(
            "SELECT COUNT(*) FROM inventory_reports WHERE retailer_name ILIKE 'Winthrop Variety%'"
        )
        print(f"  inventory_reports by retailer_name 'Winthrop Variety*': {n_name}")
        # Sweep by name in case of variant
        if n_name:
            await conn.execute(
                "DELETE FROM inventory_reports WHERE retailer_name ILIKE 'Winthrop Variety%'"
            )
            print("  swept by retailer_name")
        u = await conn.fetchval("SELECT COUNT(*) FROM users WHERE email=$1", EMAIL)
        print(f"  users by email: {u}")
    finally:
        await conn.close()


asyncio.run(main())
