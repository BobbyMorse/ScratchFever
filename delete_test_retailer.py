"""
Deletes the Winthrop Variety test retailer account.
Run: python delete_test_retailer.py
"""
import asyncio
import os

from dotenv import load_dotenv
load_dotenv()

import asyncpg

EMAIL = "teststore@scratchfever.app"


async def main():
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)

    try:
        user = await conn.fetchrow("SELECT id FROM users WHERE email=$1", EMAIL)
        if not user:
            print(f"No account found for {EMAIL}")
            return

        user_id = user["id"]
        await conn.execute("DELETE FROM retailer_profiles WHERE user_id=$1", user_id)
        await conn.execute("DELETE FROM users WHERE id=$1", user_id)
        print(f"Deleted retailer account ({EMAIL}, user_id={user_id})")

    finally:
        await conn.close()


asyncio.run(main())
