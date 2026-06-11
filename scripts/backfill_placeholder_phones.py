"""
One-shot backfill: null out lottery-feed placeholder phone numbers.

Lottery feeds (MA confirmed, others suspected) report 9999999999 / 0000000000
for retailers with no phone on file. These leak into ma_retailers.phone and
state_retailers.phone, then surface as "callable" candidates in the VAPI
dispatcher. Dialing them burns Twilio transport-error quota and eventually
trips the per-number daily-exhaustion threshold for legitimately working
numbers.

Read-time filtering is now in place (vapi_dispatch._is_placeholder_phone), so
this script is a cleanup, not a correctness requirement. Run once.

Usage:
    python scripts/backfill_placeholder_phones.py
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
logger = logging.getLogger("backfill_placeholder_phones")

PLACEHOLDERS = ("9999999999", "0000000000", "1111111111")


async def _scrub_table(conn, table: str, set_null: bool) -> int:
    """Return count of rows updated. If set_null is False, blanks the column
    instead (state_retailers' phone column may be NOT NULL on some setups)."""
    placeholders_sql = ", ".join(f"'{p}'" for p in PLACEHOLDERS)
    new_value = "NULL" if set_null else "''"
    res = await conn.execute(
        f"""UPDATE {table}
            SET phone = {new_value}
            WHERE phone IS NOT NULL
              AND phone <> ''
              AND RIGHT(REGEXP_REPLACE(phone, '\\D', '', 'g'), 10) IN ({placeholders_sql})"""
    )
    try:
        return int(res.rsplit(" ", 1)[-1])
    except Exception:
        return 0


async def main() -> None:
    await init_db()
    async with get_pool().acquire() as conn:
        ma_count = await _scrub_table(conn, "ma_retailers", set_null=True)
        logger.info("ma_retailers: cleared %d placeholder phones", ma_count)
        sr_count = await _scrub_table(conn, "state_retailers", set_null=True)
        logger.info("state_retailers: cleared %d placeholder phones", sr_count)
        az_exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'az_retailers'"
        )
        if az_exists:
            az_count = await _scrub_table(conn, "az_retailers", set_null=True)
            logger.info("az_retailers: cleared %d placeholder phones", az_count)
        logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
