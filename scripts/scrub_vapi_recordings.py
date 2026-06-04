"""
One-time scrub of historical VAPI calls.

Two-party-consent compliance:
  1. DELETE every past call from VAPI (removes their stored audio + transcript)
  2. Wipe `transcript` and `raw_payload` columns from local vapi_calls rows

The structured inventory fields (per_ticket_results, has_game, summary,
durations, retailer match, etc.) are preserved — those are inventory notes,
not a recording.

Usage:
    export VAPI_PRIVATE_KEY="..."
    # Bash:
    export DATABASE_URL="postgres://..."
    python scripts/scrub_vapi_recordings.py
"""
from __future__ import annotations
import asyncio
import os
import sys

import httpx

# Backend imports — script must be run from project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.database import get_pool, init_db  # noqa: E402


async def _delete_on_vapi(client: httpx.AsyncClient, key: str, vapi_call_id: str) -> str:
    try:
        r = await client.delete(
            f"https://api.vapi.ai/call/{vapi_call_id}",
            headers={"Authorization": f"Bearer {key}"},
        )
        if r.status_code in (200, 204):
            return "deleted"
        if r.status_code == 404:
            return "already-gone"
        return f"http-{r.status_code}"
    except Exception as exc:
        return f"err:{type(exc).__name__}"


async def main() -> int:
    key = os.environ.get("VAPI_PRIVATE_KEY")
    if not key:
        print("ERROR: VAPI_PRIVATE_KEY not set")
        return 1
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set")
        return 1

    await init_db()
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, vapi_call_id FROM vapi_calls WHERE vapi_call_id IS NOT NULL"
        )

    print(f"Found {len(rows)} VAPI-side calls to delete.")

    deleted = 0
    counters: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, r in enumerate(rows, 1):
            status = await _delete_on_vapi(client, key, r["vapi_call_id"])
            counters[status] = counters.get(status, 0) + 1
            if status in ("deleted", "already-gone"):
                deleted += 1
            if i % 25 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)}  {counters}")

    print(f"\nVAPI-side: {deleted}/{len(rows)} cleared. Breakdown: {counters}")

    # Wipe local conversation content.
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE vapi_calls SET transcript = NULL, raw_payload = NULL "
            "WHERE transcript IS NOT NULL OR raw_payload IS NOT NULL"
        )
    print(f"Local DB wipe: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
