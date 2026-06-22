"""
One-shot backfill: URL-decode DC retailer phone numbers in state_retailers.

The DC scraper (fetch_dc_retailers.py) historically pulled phone numbers from
<a href="tel:..."> attributes without URL-decoding, so '(' and ')' landed in
the DB as '%28' and '%29' (e.g. '%28202%29833-8730'). The scraper is now
fixed; this script repairs the rows already loaded.

Usage:
    python scripts/fix_dc_phone_encoding.py
"""
from __future__ import annotations
import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from backend.database import init_db, get_pool  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("fix_dc_phone_encoding")


async def main() -> None:
    await init_db()
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, phone FROM state_retailers
               WHERE state_code = 'DC'
                 AND phone IS NOT NULL
                 AND phone LIKE '%\\%2%' ESCAPE '\\'"""
        )
        logger.info("Found %d DC rows with percent-encoded phone", len(rows))

        updated = 0
        for r in rows:
            fixed = unquote(r["phone"]).strip()
            if fixed == r["phone"]:
                continue
            await conn.execute(
                "UPDATE state_retailers SET phone = $1 WHERE id = $2",
                fixed, r["id"],
            )
            updated += 1

        logger.info("Updated %d rows.", updated)


if __name__ == "__main__":
    asyncio.run(main())
