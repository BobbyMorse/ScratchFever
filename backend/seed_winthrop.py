"""
One-off: link robertmorse73@gmail.com to "Winthrop Variety" (MA) so we can
exercise the My Store dashboard end-to-end without going through the claim flow.

Idempotent — re-running updates the existing profile in place.

Usage (from repo root, with DATABASE_URL set):
    python -m backend.seed_winthrop
"""
from __future__ import annotations
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv()

EMAIL = "robertmorse73@gmail.com"
STORE_NAME = "Winthrop Variety"
STATE = "MA"
CITY = "Winthrop"


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set. Export it or add to backend/.env first.", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(db_url, statement_cache_size=0)
    try:
        # 0. Make sure the schema additions from this PR exist before we INSERT
        # rows that reference them. App startup also runs these, but seeding
        # against a freshly-deployed prod DB may race the lifespan handler.
        await conn.execute("ALTER TABLE retailer_profiles ADD COLUMN IF NOT EXISTS description TEXT")
        await conn.execute("ALTER TABLE retailer_profiles ADD COLUMN IF NOT EXISTS website TEXT")
        await conn.execute("ALTER TABLE retailer_profiles ADD COLUMN IF NOT EXISTS contact_email TEXT")
        await conn.execute("ALTER TABLE retailer_profiles ADD COLUMN IF NOT EXISTS hours_text TEXT")
        await conn.execute("ALTER TABLE retailer_profiles ADD COLUMN IF NOT EXISTS photo_url TEXT")
        await conn.execute("ALTER TABLE retailer_profiles ADD COLUMN IF NOT EXISTS banner_text TEXT")
        await conn.execute("ALTER TABLE retailer_profiles ADD COLUMN IF NOT EXISTS banner_until TIMESTAMPTZ")
        await conn.execute("ALTER TABLE retailer_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS retailer_claims (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                retailer_id TEXT NOT NULL,
                state_code TEXT NOT NULL,
                store_name TEXT NOT NULL,
                city TEXT, zip TEXT, phone TEXT,
                claimant_role TEXT, claimant_name TEXT, claimant_phone TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                reviewed_at TIMESTAMPTZ,
                reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                review_notes TEXT
            )
        """)

        # 1. Find user
        user = await conn.fetchrow(
            "SELECT id, email, username, role FROM users WHERE email=$1",
            EMAIL,
        )
        if not user:
            print(f"ERROR: user {EMAIL} not found — register at /?retailer_redirect=1 first.", file=sys.stderr)
            sys.exit(1)
        print(f"Found user: id={user['id']} email={user['email']} role={user['role']}")

        # 2. Insert (or reuse) a real ma_retailers row for "Winthrop Variety"
        # so the store is discoverable in the consumer-side MA feed + map.
        # MA-specific because the MA list is served from ma_retailers (not state_retailers).
        phone = "(617) 555-0142"
        zip_code = "02152"
        address = "100 Main St"
        # Winthrop, MA centerish — far enough from other shops to stand out on the map
        latitude = 42.3751
        longitude = -70.9786

        existing_ma = await conn.fetchrow(
            """SELECT id FROM ma_retailers
               WHERE LOWER(name)=LOWER($1) AND LOWER(city)=LOWER($2)
               LIMIT 1""",
            STORE_NAME, CITY,
        )
        if existing_ma:
            retailer_id = str(existing_ma["id"])
            # Make sure the existing row is active and has up-to-date demo data
            await conn.execute(
                """UPDATE ma_retailers SET
                     address=$2, zip_code=$3, phone=$4,
                     latitude=$5, longitude=$6, is_active=TRUE
                   WHERE id=$1""",
                int(retailer_id), address, zip_code, phone, latitude, longitude,
            )
            print(f"Reusing existing ma_retailers row #{retailer_id}: {STORE_NAME}")
        else:
            # ma_retailers.id is not auto-increment (real rows come from MA's
            # lottery export with their own IDs). Pick an id well above the max
            # so future imports don't collide.
            max_id = await conn.fetchval("SELECT COALESCE(MAX(id), 0) FROM ma_retailers")
            new_id = max(max_id + 1, 999_000_000)  # always demo-range
            new_row = await conn.fetchrow(
                """INSERT INTO ma_retailers
                   (id, name, address, city, zip_code, phone, latitude, longitude,
                    is_active, is_chain, is_gas)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,TRUE,FALSE,FALSE)
                   RETURNING id""",
                new_id, STORE_NAME, address, CITY, zip_code, phone, latitude, longitude,
            )
            retailer_id = str(new_row["id"])
            print(f"Created ma_retailers row #{retailer_id}: {STORE_NAME}")

        # 3. Upsert retailer_profiles row, bump user role, in one transaction
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id, retailer_id FROM retailer_profiles WHERE user_id=$1",
                user["id"],
            )
            if existing:
                await conn.execute(
                    """UPDATE retailer_profiles SET
                         retailer_id=$2, state_code=$3, store_name=$4,
                         city=$5, zip=$6, phone=$7, verified=TRUE,
                         description = COALESCE(description, $8),
                         hours_text  = COALESCE(hours_text, $9),
                         updated_at  = NOW()
                       WHERE user_id=$1""",
                    user["id"], retailer_id, STATE, STORE_NAME,
                    CITY, zip_code, phone,
                    "Family-owned corner store with a generous lottery section. Coffee, cold drinks, and the best $30 game selection in town.",
                    "Mon–Sat 6am–11pm · Sun 7am–10pm",
                )
                print(f"Updated existing retailer_profile #{existing['id']} (was retailer_id={existing['retailer_id']})")
            else:
                row = await conn.fetchrow(
                    """INSERT INTO retailer_profiles
                       (user_id, retailer_id, state_code, store_name, city, zip, phone,
                        verified, description, hours_text)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE,$8,$9)
                       RETURNING id""",
                    user["id"], retailer_id, STATE, STORE_NAME,
                    CITY, zip_code, phone,
                    "Family-owned corner store with a generous lottery section. Coffee, cold drinks, and the best $30 game selection in town.",
                    "Mon–Sat 6am–11pm · Sun 7am–10pm",
                )
                print(f"Created retailer_profile #{row['id']}")

            await conn.execute(
                "UPDATE users SET role='retailer' WHERE id=$1 AND role='member'",
                user["id"],
            )

        final = await conn.fetchrow(
            "SELECT role FROM users WHERE id=$1", user["id"]
        )
        print(f"User role is now: {final['role']}")
        print("")
        print("Done. Log in at /?retailer_redirect=1 and visit /retailer to test.")
        if user["role"] in ("admin",):
            print("(User is admin — role NOT bumped; admins keep their admin role and still get retailer access.)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
