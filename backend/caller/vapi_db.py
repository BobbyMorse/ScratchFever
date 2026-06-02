"""
Storage for VAPI call results.

VAPI runs the actual phone calls (telephony + STT + LLM + TTS) externally.
We receive end-of-call webhooks and persist the result here, then mirror
positive/negative inventory findings into the public inventory_reports table.
"""
from __future__ import annotations
import json
from typing import Any, Optional

from backend.database import get_pool


async def init_vapi_db() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vapi_calls (
                id SERIAL PRIMARY KEY,
                vapi_call_id TEXT UNIQUE,
                received_at TIMESTAMPTZ DEFAULT NOW(),
                started_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                duration_sec REAL,
                ended_reason TEXT,
                to_phone TEXT,
                from_phone TEXT,
                state_code TEXT,
                retailer_external_id TEXT,
                retailer_name TEXT,
                retailer_city TEXT,
                game_name TEXT,
                game_price REAL,
                game_number TEXT,
                has_game BOOLEAN,
                confidence REAL,
                can_order BOOLEAN,
                summary TEXT,
                notes TEXT,
                transcript TEXT,
                per_ticket_results JSONB,
                is_voicemail BOOLEAN,
                raw_payload JSONB
            )
        """)
        # Backfill for existing prod DBs that predate the per_ticket_results column.
        await conn.execute(
            "ALTER TABLE vapi_calls ADD COLUMN IF NOT EXISTS per_ticket_results JSONB"
        )
        await conn.execute(
            "ALTER TABLE vapi_calls ADD COLUMN IF NOT EXISTS is_voicemail BOOLEAN"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vapi_received ON vapi_calls(received_at DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vapi_phone ON vapi_calls(to_phone)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vapi_retailer ON vapi_calls(retailer_external_id)"
        )


async def insert_vapi_call(row: dict[str, Any]) -> int:
    raw = row.get("raw_payload")
    if raw is not None and not isinstance(raw, str):
        raw = json.dumps(raw, default=str)

    per_ticket = row.get("per_ticket_results")
    if per_ticket is not None and not isinstance(per_ticket, str):
        per_ticket = json.dumps(per_ticket, default=str)

    pool = get_pool()
    async with pool.acquire() as conn:
        new_id = await conn.fetchval(
            """
            INSERT INTO vapi_calls (
                vapi_call_id, started_at, ended_at, duration_sec, ended_reason,
                to_phone, from_phone, state_code,
                retailer_external_id, retailer_name, retailer_city,
                game_name, game_price, game_number,
                has_game, confidence, can_order,
                summary, notes, transcript, per_ticket_results, is_voicemail, raw_payload
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8,
                $9, $10, $11,
                $12, $13, $14,
                $15, $16, $17,
                $18, $19, $20, $21::jsonb, $22, $23::jsonb
            )
            ON CONFLICT (vapi_call_id) DO UPDATE SET
                ended_at           = COALESCE(EXCLUDED.ended_at,           vapi_calls.ended_at),
                duration_sec       = COALESCE(EXCLUDED.duration_sec,       vapi_calls.duration_sec),
                ended_reason       = COALESCE(EXCLUDED.ended_reason,       vapi_calls.ended_reason),
                has_game           = COALESCE(EXCLUDED.has_game,           vapi_calls.has_game),
                confidence         = COALESCE(EXCLUDED.confidence,         vapi_calls.confidence),
                can_order          = COALESCE(EXCLUDED.can_order,          vapi_calls.can_order),
                summary            = COALESCE(EXCLUDED.summary,            vapi_calls.summary),
                notes              = COALESCE(EXCLUDED.notes,              vapi_calls.notes),
                transcript         = COALESCE(EXCLUDED.transcript,         vapi_calls.transcript),
                per_ticket_results = COALESCE(EXCLUDED.per_ticket_results, vapi_calls.per_ticket_results),
                is_voicemail       = COALESCE(EXCLUDED.is_voicemail,       vapi_calls.is_voicemail),
                raw_payload        = EXCLUDED.raw_payload
            RETURNING id
            """,
            row.get("vapi_call_id"),
            row.get("started_at"),
            row.get("ended_at"),
            row.get("duration_sec"),
            row.get("ended_reason"),
            row.get("to_phone"),
            row.get("from_phone"),
            row.get("state_code"),
            row.get("retailer_external_id"),
            row.get("retailer_name"),
            row.get("retailer_city"),
            row.get("game_name"),
            row.get("game_price"),
            row.get("game_number"),
            row.get("has_game"),
            row.get("confidence"),
            row.get("can_order"),
            row.get("summary"),
            row.get("notes"),
            row.get("transcript"),
            per_ticket,
            row.get("is_voicemail"),
            raw,
        )
        return new_id


async def find_retailer_by_phone(phone_digits: str) -> Optional[dict]:
    """Look up state_retailers by last-10-digits of phone number."""
    if not phone_digits or len(phone_digits) < 10:
        return None
    last10 = phone_digits[-10:]
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT state_code, external_id, name, city, latitude, longitude
            FROM state_retailers
            WHERE RIGHT(REGEXP_REPLACE(phone, '[^0-9]', '', 'g'), 10) = $1
              AND is_active = TRUE
            LIMIT 1
            """,
            last10,
        )
    return dict(row) if row else None


async def recent_vapi_calls(limit: int = 50) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, vapi_call_id, received_at, ended_at, duration_sec, ended_reason,
                   to_phone, state_code, retailer_external_id, retailer_name, retailer_city,
                   game_name, game_price, has_game, confidence, can_order, summary, notes,
                   transcript, per_ticket_results
            FROM vapi_calls
            ORDER BY received_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]
