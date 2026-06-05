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
        from backend.database import add_column_if_missing
        await add_column_if_missing(conn, "vapi_calls", "per_ticket_results", "JSONB")
        await add_column_if_missing(conn, "vapi_calls", "is_voicemail", "BOOLEAN")
        # live_status tracks the VAPI status-update lifecycle (queued, ringing,
        # in-progress, forwarding, ended). Lets the UI show real-time progress
        # without polling VAPI directly.
        await add_column_if_missing(conn, "vapi_calls", "live_status", "TEXT")
        # Call-funnel and disposition fields extracted by VAPI's analysisPlan.
        # We persist them as discrete columns so the dashboard can show
        # per-call performance ("answered? confirmed? actually checked?") at
        # a glance and filter on them later.
        await add_column_if_missing(conn, "vapi_calls", "answered_phone",             "BOOLEAN")
        await add_column_if_missing(conn, "vapi_calls", "confirmed_sells_scratch",    "BOOLEAN")
        await add_column_if_missing(conn, "vapi_calls", "inventory_actually_checked", "BOOLEAN")
        await add_column_if_missing(conn, "vapi_calls", "tickets_asked_count",        "INTEGER")
        await add_column_if_missing(conn, "vapi_calls", "tickets_answered_count",     "INTEGER")
        await add_column_if_missing(conn, "vapi_calls", "customer_disposition",       "TEXT")
        await add_column_if_missing(conn, "vapi_calls", "ended_early_reason",         "TEXT")
        # Set when per_ticket_results were mirrored into inventory_reports so
        # backfills/reconciles can stay idempotent (no duplicate inventory writes).
        await add_column_if_missing(conn, "vapi_calls", "inventory_mirrored_at",      "TIMESTAMPTZ")
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
                summary, notes, transcript, per_ticket_results, is_voicemail, raw_payload,
                answered_phone, confirmed_sells_scratch, inventory_actually_checked,
                tickets_asked_count, tickets_answered_count,
                customer_disposition, ended_early_reason
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8,
                $9, $10, $11,
                $12, $13, $14,
                $15, $16, $17,
                $18, $19, $20, $21::jsonb, $22, $23::jsonb,
                $24, $25, $26,
                $27, $28,
                $29, $30
            )
            ON CONFLICT (vapi_call_id) DO UPDATE SET
                ended_at                   = COALESCE(EXCLUDED.ended_at,                   vapi_calls.ended_at),
                duration_sec               = COALESCE(EXCLUDED.duration_sec,               vapi_calls.duration_sec),
                ended_reason               = COALESCE(EXCLUDED.ended_reason,               vapi_calls.ended_reason),
                has_game                   = COALESCE(EXCLUDED.has_game,                   vapi_calls.has_game),
                confidence                 = COALESCE(EXCLUDED.confidence,                 vapi_calls.confidence),
                can_order                  = COALESCE(EXCLUDED.can_order,                  vapi_calls.can_order),
                summary                    = COALESCE(EXCLUDED.summary,                    vapi_calls.summary),
                notes                      = COALESCE(EXCLUDED.notes,                      vapi_calls.notes),
                transcript                 = COALESCE(EXCLUDED.transcript,                 vapi_calls.transcript),
                per_ticket_results         = COALESCE(EXCLUDED.per_ticket_results,         vapi_calls.per_ticket_results),
                is_voicemail               = COALESCE(EXCLUDED.is_voicemail,               vapi_calls.is_voicemail),
                raw_payload                = EXCLUDED.raw_payload,
                answered_phone             = COALESCE(EXCLUDED.answered_phone,             vapi_calls.answered_phone),
                confirmed_sells_scratch    = COALESCE(EXCLUDED.confirmed_sells_scratch,    vapi_calls.confirmed_sells_scratch),
                inventory_actually_checked = COALESCE(EXCLUDED.inventory_actually_checked, vapi_calls.inventory_actually_checked),
                tickets_asked_count        = COALESCE(EXCLUDED.tickets_asked_count,        vapi_calls.tickets_asked_count),
                tickets_answered_count     = COALESCE(EXCLUDED.tickets_answered_count,     vapi_calls.tickets_answered_count),
                customer_disposition       = COALESCE(EXCLUDED.customer_disposition,       vapi_calls.customer_disposition),
                ended_early_reason         = COALESCE(EXCLUDED.ended_early_reason,         vapi_calls.ended_early_reason)
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
            row.get("answered_phone"),
            row.get("confirmed_sells_scratch"),
            row.get("inventory_actually_checked"),
            row.get("tickets_asked_count"),
            row.get("tickets_answered_count"),
            row.get("customer_disposition"),
            row.get("ended_early_reason"),
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


async def delete_vapi_call(call_id: int) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM vapi_calls WHERE id = $1", call_id)
    return result.endswith(" 1")


async def recent_vapi_calls(limit: int = 50) -> list[dict]:
    """Returns rows for the dashboard "Recent VAPI calls" table. Includes
    test calls (external_id='test') so the user can verify end-to-end flow
    using the Send Test Call button — the UI's own copy promises they show
    up here. Test calls are still excluded from inventory_reports mirroring
    at webhook time; this only governs the dashboard."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, vapi_call_id, received_at, started_at, ended_at, duration_sec, ended_reason,
                   to_phone, state_code, retailer_external_id, retailer_name, retailer_city,
                   game_name, game_price, has_game, confidence, can_order, summary, notes,
                   transcript, per_ticket_results, is_voicemail, live_status,
                   answered_phone, confirmed_sells_scratch, inventory_actually_checked,
                   tickets_asked_count, tickets_answered_count,
                   customer_disposition, ended_early_reason
            FROM vapi_calls
            ORDER BY received_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]
