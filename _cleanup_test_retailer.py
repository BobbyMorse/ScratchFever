"""Temporary cleanup: nuke any rows tied to the Winthrop Variety test retailer."""
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
        # Show what's there first
        for tbl, col in [
            ("inventory_reports", "retailer_id"),
            ("retailer_posts",    "retailer_id"),
            ("retailer_profiles", "retailer_id"),
        ]:
            n = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl} WHERE {col}=$1", RET_ID)
            print(f"  {tbl}: {n}")
        u = await conn.fetchval("SELECT COUNT(*) FROM users WHERE email=$1", EMAIL)
        print(f"  users (by email): {u}")

        # Also check by store_name in case retailer_id differs
        n_by_name = await conn.fetchval(
            "SELECT COUNT(*) FROM retailer_profiles WHERE store_name ILIKE 'Winthrop Variety%'"
        )
        print(f"  retailer_profiles (by store_name 'Winthrop Variety*'): {n_by_name}")

        # Delete everything tied to RET_ID or the email
        await conn.execute("DELETE FROM inventory_reports WHERE retailer_id=$1", RET_ID)
        await conn.execute("DELETE FROM retailer_posts    WHERE retailer_id=$1", RET_ID)

        # Any retailer_profiles row with that retailer_id, OR matching the test store_name
        user_ids = await conn.fetch(
            """SELECT user_id FROM retailer_profiles
               WHERE retailer_id=$1 OR store_name ILIKE 'Winthrop Variety%'""",
            RET_ID,
        )
        await conn.execute(
            """DELETE FROM retailer_profiles
               WHERE retailer_id=$1 OR store_name ILIKE 'Winthrop Variety%'""",
            RET_ID,
        )
        for row in user_ids:
            await conn.execute("DELETE FROM users WHERE id=$1", row["user_id"])

        # And the email row if it still exists
        await conn.execute("DELETE FROM users WHERE email=$1", EMAIL)

        print("Cleanup done.")
    finally:
        await conn.close()


asyncio.run(main())
