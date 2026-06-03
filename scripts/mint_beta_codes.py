"""
Mint one-time-use beta codes that grant N days of Pro.

Inserts directly into the beta_codes table (same logic the admin API uses),
so it works whether or not the API server is running.

Usage:
    python scripts/mint_beta_codes.py                       # 10 codes, 180 days
    python scripts/mint_beta_codes.py --count 25
    python scripts/mint_beta_codes.py --count 5 --days 365 --note "TikTok launch"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _generate_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


async def mint(count: int, days: int, note: str | None) -> list[str]:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    conn = await asyncpg.connect(dsn)
    try:
        codes: list[str] = []
        attempts = 0
        while len(codes) < count and attempts < count * 4:
            attempts += 1
            code = _generate_code()
            try:
                await conn.execute(
                    "INSERT INTO beta_codes (code, duration_days, note) VALUES ($1, $2, $3)",
                    code, days, note,
                )
                codes.append(code)
            except asyncpg.UniqueViolationError:
                continue
        return codes
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--note", default=None)
    args = ap.parse_args()

    if args.count < 1 or args.count > 1000:
        print("count must be 1-1000", file=sys.stderr)
        return 1

    codes = asyncio.run(mint(args.count, args.days, args.note))
    print(f"Minted {len(codes)} code(s) — {args.days} days each"
          + (f"  ({args.note})" if args.note else ""))
    print()
    for c in codes:
        print(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
