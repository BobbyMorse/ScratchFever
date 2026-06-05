"""
Backfill missing analysis data into local vapi_calls rows.

For every row that has a vapi_call_id but is missing summary or structured-data
fields, fetch GET /call/{id} from VAPI and persist:
  - summary, notes (summary_notes)
  - per_ticket_results
  - answered_phone, confirmed_sells_scratch, inventory_actually_checked
  - tickets_asked_count, tickets_answered_count
  - customer_disposition, ended_early_reason

Run when end-of-call-report webhooks have been dropped or after a schema/analysis
change to recover the data from VAPI's side.

Usage:
    export VAPI_PRIVATE_KEY="..."
    export DATABASE_URL="postgres://..."
    python scripts/backfill_vapi_analysis.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys

import re

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.database import get_pool, init_db, add_inventory_report  # noqa: E402


def _split_asked_names(asked_field):
    if not asked_field:
        return []
    return [n.strip() for n in str(asked_field).split(",") if n.strip()]


def _canonical_ticket_name(reported, asked):
    """Bot occasionally drifts on case/spacing ('300 x' -> '300X'). Map back
    to whichever name we actually asked about, so the inventory dashboard's
    exact-name lookup matches. Falls back to reported when no match."""
    if not reported or not asked:
        return reported
    target = re.sub(r"\s+", "", reported).lower()
    for canonical in asked:
        if re.sub(r"\s+", "", canonical).lower() == target:
            return canonical
    return reported


def _to_bool(v):
    if v is None: return None
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)): return bool(v)
    s = str(v).strip().lower()
    if s in ("true","yes","y","1"):  return True
    if s in ("false","no","n","0"):  return False
    return None


def _to_int(v):
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


async def main() -> int:
    key = os.environ.get("VAPI_PRIVATE_KEY")
    db  = os.environ.get("DATABASE_URL")
    if not key or not db:
        print("ERROR: VAPI_PRIVATE_KEY and DATABASE_URL must be set.")
        return 1

    await init_db()
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, vapi_call_id, retailer_external_id, retailer_name, retailer_city,
                   to_phone, ended_at, is_voicemail, inventory_mirrored_at, game_name
            FROM vapi_calls
            WHERE vapi_call_id IS NOT NULL
              AND (summary IS NULL OR customer_disposition IS NULL
                   OR (inventory_mirrored_at IS NULL AND per_ticket_results IS NOT NULL))
              AND received_at > NOW() - INTERVAL '7 days'
            ORDER BY received_at DESC
            LIMIT 200
        """)
    print(f"Backfilling {len(rows)} rows.")

    updated = 0
    inv_written = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for r in rows:
            try:
                resp = await client.get(
                    f"https://api.vapi.ai/call/{r['vapi_call_id']}",
                    headers={"Authorization": f"Bearer {key}"},
                )
            except Exception as e:
                print(f"  err GET {r['vapi_call_id']}: {e}")
                continue
            if resp.status_code != 200:
                continue
            d = resp.json()
            analysis = d.get("analysis") or {}
            structured = analysis.get("structuredData") or {}
            summary = analysis.get("summary")
            notes   = structured.get("summary_notes")
            per_ticket = structured.get("per_ticket_results")
            per_ticket_json = json.dumps(per_ticket, default=str) if per_ticket else None

            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE vapi_calls SET
                        summary                    = COALESCE(summary,                    $2),
                        notes                      = COALESCE(notes,                      $3),
                        per_ticket_results         = COALESCE(per_ticket_results,         $4::jsonb),
                        answered_phone             = COALESCE(answered_phone,             $5),
                        confirmed_sells_scratch    = COALESCE(confirmed_sells_scratch,    $6),
                        inventory_actually_checked = COALESCE(inventory_actually_checked, $7),
                        tickets_asked_count        = COALESCE(tickets_asked_count,        $8),
                        tickets_answered_count     = COALESCE(tickets_answered_count,     $9),
                        customer_disposition       = COALESCE(customer_disposition,       $10),
                        ended_early_reason         = COALESCE(ended_early_reason,         $11)
                    WHERE id = $1
                """,
                    r["id"],
                    summary,
                    notes,
                    per_ticket_json,
                    _to_bool(structured.get("answered_phone")),
                    _to_bool(structured.get("confirmed_sells_scratch")),
                    _to_bool(structured.get("inventory_actually_checked")),
                    _to_int (structured.get("tickets_asked_count")),
                    _to_int (structured.get("tickets_answered_count")),
                    structured.get("customer_disposition"),
                    structured.get("ended_early_reason"),
                )
            updated += 1

            # Mirror per-ticket findings into inventory_reports — but only once
            # per call (inventory_mirrored_at is the idempotency flag).
            already_mirrored = r["inventory_mirrored_at"] is not None
            has_retailer = bool(r["retailer_external_id"]) and r["retailer_external_id"] != "test"
            if not already_mirrored and has_retailer and per_ticket and not r["is_voicemail"]:
                rows_this_call = 0
                async with pool.acquire() as conn:
                    for t in per_ticket:
                        if not isinstance(t, dict):
                            continue
                        t_name = (t.get("name") or "").strip()
                        t_has  = _to_bool(t.get("has_game"))
                        if not t_name or t_has is None:
                            continue
                        await add_inventory_report(
                            conn,
                            retailer_id=r["retailer_external_id"],
                            retailer_name=r["retailer_name"],
                            retailer_city=r["retailer_city"],
                            game_name=t_name,
                            game_price=None,
                            has_stock=bool(t_has),
                            source="vapi_call",
                            reporter_username="vapi",
                            notes=(t.get("notes") or None),
                            reported_at=r["ended_at"],
                        )
                        rows_this_call += 1
                if rows_this_call > 0:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE vapi_calls SET inventory_mirrored_at = NOW() WHERE id = $1",
                            r["id"],
                        )
                    inv_written += rows_this_call
    print(f"Updated {updated} rows.  Mirrored {inv_written} inventory rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
