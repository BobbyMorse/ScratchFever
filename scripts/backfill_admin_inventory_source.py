"""
One-shot backfill: re-tag existing admin-submitted inventory reports.

Before fix: every report through /api/inventory/report was stamped
`source='community'` regardless of who submitted it, so admin entries
bucketed into the user-facing "Member Reports" section.

This script updates existing `inventory_reports` rows whose
`reporter_username` matches a `users.role = 'admin'` row, flipping
`source` from 'community' to 'admin' so the frontend re-buckets them
into the operator/BRAND-verified section.

Idempotent: rows already at source='admin' (or anything other than
'community') are untouched.

Usage:
    python scripts/backfill_admin_inventory_source.py
"""
from __future__ import annotations
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from backend.database import init_db, get_pool  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("backfill_admin_source")


async def main() -> None:
    await init_db()
    async with get_pool().acquire() as conn:
        admin_usernames = await conn.fetch(
            "SELECT username FROM users WHERE role = 'admin' AND username IS NOT NULL"
        )
        usernames = [r["username"] for r in admin_usernames]
        if not usernames:
            logger.info("No admin users with a username — nothing to backfill.")
            return
        logger.info("Found %d admin username(s): %s", len(usernames), usernames)

        affected = await conn.fetchval(
            """
            SELECT COUNT(*) FROM inventory_reports
            WHERE source = 'community' AND reporter_username = ANY($1::text[])
            """,
            usernames,
        )
        logger.info("Rows to update: %d", affected)
        if not affected:
            return

        await conn.execute(
            """
            UPDATE inventory_reports
               SET source = 'admin'
             WHERE source = 'community'
               AND reporter_username = ANY($1::text[])
            """,
            usernames,
        )
        logger.info("Backfill complete: %d rows updated.", affected)


if __name__ == "__main__":
    asyncio.run(main())
