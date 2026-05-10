"""
Creates a single test retailer account for portal testing.
Run: python create_test_retailer.py
"""
import asyncio
import hashlib
import hmac
import os
import secrets

from dotenv import load_dotenv
load_dotenv()

import asyncpg

EMAIL    = "teststore@scratchfever.app"
PASSWORD = "TestStore123!"
STORE    = "Winthrop Variety"
CITY     = "Winthrop"
ZIP      = "02152"
PHONE    = "617-555-0101"
RET_ID   = "TEST-WINTHROP-001"
STATE    = "MA"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key  = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{key.hex()}"


async def main():
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)

    try:
        existing = await conn.fetchrow("SELECT id FROM users WHERE email=$1", EMAIL)
        if existing:
            print(f"Account already exists (user_id={existing['id']})")
            print(f"  Email:    {EMAIL}")
            print(f"  Password: {PASSWORD}")
            return

        user_id = await conn.fetchval(
            "INSERT INTO users (email, password_hash, role) VALUES ($1,$2,'retailer') RETURNING id",
            EMAIL, hash_password(PASSWORD),
        )

        await conn.execute(
            """INSERT INTO retailer_profiles
               (user_id, retailer_id, state_code, store_name, city, zip, phone, verified)
               VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE)""",
            user_id, RET_ID, STATE, STORE, CITY, ZIP, PHONE,
        )

        print("Retailer account created!")
        print(f"  Email:    {EMAIL}")
        print(f"  Password: {PASSWORD}")
        print(f"  Store:    {STORE}, {CITY} {ZIP}")
        print(f"  Login at: http://localhost:8000/retailer.html")

    finally:
        await conn.close()


asyncio.run(main())
