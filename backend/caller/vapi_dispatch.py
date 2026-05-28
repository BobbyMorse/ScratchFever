"""
Outbound VAPI dispatch — admin-only.

VAPI runs the actual phone calls externally (telephony + STT + LLM + TTS).
This module:
  1. Selects retailers from our DB (state_retailers + state-specific scorers
     for MA/AZ where available)
  2. POSTs to VAPI's REST API to create outbound calls with per-call context
     passed via assistantOverrides.variableValues
  3. Inserts placeholder rows into vapi_calls so the dashboard can show the
     dispatch immediately. The webhook (vapi_webhook.py) later UPSERTs the
     same row with the end-of-call data.

Environment:
  VAPI_PRIVATE_KEY      Bearer token for api.vapi.ai (server-side key)
  VAPI_ASSISTANT_ID     Assistant to run on each call
  VAPI_PHONE_NUMBER_ID  VAPI-managed number to dial from

Routes:
  GET  /api/vapi/config              env-var presence check
  GET  /api/vapi/stats               counts for dashboard cards
  GET  /api/vapi/states              distinct state codes with callable phones
  GET  /api/vapi/retailers           filtered pull from state_retailers
  POST /api/vapi/dispatch            ad-hoc dispatch by explicit retailer_ids
  POST /api/vapi/dispatch_campaign   "campaign-style" dispatch by state+max_stores
  POST /api/vapi/test_call           single test call to an arbitrary number
"""
from __future__ import annotations
import asyncio
import datetime as dt
import json
import logging
import os
import re
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.database import get_pool
from backend.users import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vapi", tags=["vapi"])

VAPI_API_BASE = "https://api.vapi.ai"
MAX_BATCH = 500
CONCURRENCY = 5


def _vapi_env() -> dict[str, Optional[str]]:
    return {
        "private_key":     os.getenv("VAPI_PRIVATE_KEY"),
        "assistant_id":    os.getenv("VAPI_ASSISTANT_ID"),
        "phone_number_id": os.getenv("VAPI_PHONE_NUMBER_ID"),
    }


def _to_e164(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if 10 <= len(digits) <= 15:
        return f"+{digits}"
    return None


# ── Retailer selection ────────────────────────────────────────────────────────

async def _select_scored_retailers(state: str, max_stores: int) -> list[dict]:
    """For MA/AZ use the scorer (highest score first). Other states fall back
    to state_retailers in unspecified order."""
    state = state.upper()
    if state == "MA":
        from backend.ma_scorer import load_and_score_async
        async with get_pool().acquire() as conn:
            scored = await load_and_score_async(conn)
        scored = [r for r in scored if r.get("phone")]
        scored.sort(key=lambda r: r.get("score", 0), reverse=True)
        if max_stores:
            scored = scored[:max_stores]
        return [{
            "external_id": str(r["id"]),
            "state_code":  "MA",
            "name":        r.get("name") or "",
            "city":        r.get("city") or "",
            "phone":       r.get("phone"),
            "score":       r.get("score"),
        } for r in scored]

    if state == "AZ":
        from backend.az_scorer import load_and_score_async
        async with get_pool().acquire() as conn:
            scored = await load_and_score_async(conn)
        scored = [r for r in scored if r.get("phone")]
        # az_scorer may or may not produce score; sort defensively
        scored.sort(key=lambda r: r.get("score", 0) or 0, reverse=True)
        if max_stores:
            scored = scored[:max_stores]
        return [{
            "external_id": str(r["id"]),
            "state_code":  "AZ",
            "name":        r.get("name") or "",
            "city":        r.get("city") or "",
            "phone":       r.get("phone"),
            "score":       r.get("score"),
        } for r in scored]

    # Generic fallback — any state with rows in state_retailers
    limit = max_stores if max_stores and max_stores > 0 else 200
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT external_id, state_code, name, city, phone
               FROM state_retailers
               WHERE state_code = $1 AND is_active = TRUE
                 AND phone IS NOT NULL AND phone <> ''
               ORDER BY city NULLS LAST, name
               LIMIT $2""",
            state, limit,
        )
    return [dict(r) | {"score": None} for r in rows]


async def _dispatch_calls(
    targets: list[dict],
    game_name: str,
    game_price: Optional[float],
    game_number: Optional[str],
    env: dict,
) -> tuple[list[dict], list[dict]]:
    """POSTs to VAPI for each target. Inserts placeholder vapi_calls rows for
    successful dispatches so the dashboard reflects them immediately.

    Returns (results, skipped).
    """
    valid: list[dict] = []
    skipped: list[dict] = []
    for t in targets:
        e164 = _to_e164(t.get("phone"))
        if not e164:
            skipped.append({"name": t.get("name"), "reason": "no valid phone"})
            continue
        valid.append({**t, "phone_e164": e164})

    sem = asyncio.Semaphore(CONCURRENCY)
    results: list[dict] = []
    placeholders: list[dict] = []

    async with httpx.AsyncClient(
        base_url=VAPI_API_BASE,
        timeout=30.0,
        headers={
            "Authorization": f"Bearer {env['private_key']}",
            "Content-Type": "application/json",
        },
    ) as client:

        async def _one(t: dict):
            payload = {
                "assistantId":    env["assistant_id"],
                "phoneNumberId":  env["phone_number_id"],
                "customer":       {"number": t["phone_e164"]},
                "assistantOverrides": {
                    "variableValues": {
                        "store_id":     t.get("external_id") or "",
                        "store_name":   t.get("name") or "",
                        "store_city":   t.get("city") or "",
                        "store_phone":  t["phone_e164"],
                        "state_code":   t.get("state_code") or "",
                        "game_name":    game_name,
                        "game_price":   game_price if game_price is not None else "",
                        "game_number":  game_number or "",
                    },
                },
            }
            async with sem:
                try:
                    resp = await client.post("/call", json=payload)
                    ok = 200 <= resp.status_code < 300
                    data: dict[str, Any] = {}
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"text": resp.text[:300]}
                    call_id = data.get("id") if ok else None
                    results.append({
                        "name":    t.get("name"),
                        "city":    t.get("city"),
                        "ok":      ok,
                        "status":  resp.status_code,
                        "call_id": call_id,
                        "error":   None if ok else (data.get("message") or data.get("text") or "unknown"),
                    })
                    if ok and call_id:
                        placeholders.append({
                            "vapi_call_id":         call_id,
                            "to_phone":             t["phone_e164"],
                            "state_code":           t.get("state_code"),
                            "retailer_external_id": t.get("external_id"),
                            "retailer_name":        t.get("name"),
                            "retailer_city":        t.get("city"),
                            "game_name":            game_name,
                            "game_price":           game_price,
                            "game_number":          game_number,
                        })
                except Exception as exc:
                    logger.exception("VAPI dispatch failed for %s", t.get("name"))
                    results.append({
                        "name":    t.get("name"),
                        "city":    t.get("city"),
                        "ok":      False,
                        "status":  0,
                        "call_id": None,
                        "error":   str(exc),
                    })

        await asyncio.gather(*(_one(t) for t in valid))

    if placeholders:
        await _insert_placeholders(placeholders)

    return results, skipped


async def _insert_placeholders(rows: list[dict]) -> None:
    """Insert pending vapi_calls rows so the dashboard can show in-flight calls
    before the end-of-call webhook arrives. The webhook UPSERTs by vapi_call_id
    so timestamps + structured fields get filled in later."""
    now = dt.datetime.now(dt.timezone.utc)
    pool = get_pool()
    async with pool.acquire() as conn:
        for r in rows:
            try:
                await conn.execute(
                    """
                    INSERT INTO vapi_calls (
                        vapi_call_id, started_at, to_phone, state_code,
                        retailer_external_id, retailer_name, retailer_city,
                        game_name, game_price, game_number, raw_payload
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                    ON CONFLICT (vapi_call_id) DO NOTHING
                    """,
                    r["vapi_call_id"], now, r["to_phone"], r["state_code"],
                    r["retailer_external_id"], r["retailer_name"], r["retailer_city"],
                    r["game_name"], r["game_price"], r["game_number"],
                    json.dumps({"source": "dispatch_placeholder"}),
                )
            except Exception:
                logger.exception("Failed to insert placeholder for call %s", r.get("vapi_call_id"))


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/config")
async def vapi_config(_user: dict = Depends(require_admin)):
    env = _vapi_env()
    return {
        "configured":       all(env.values()),
        "has_private_key":  bool(env["private_key"]),
        "has_assistant_id": bool(env["assistant_id"]),
        "has_phone_number": bool(env["phone_number_id"]),
        "assistant_id":     env["assistant_id"],
        "phone_number_id":  env["phone_number_id"],
    }


@router.get("/stats")
async def vapi_stats(_user: dict = Depends(require_admin)):
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
              COUNT(*)                                                        AS total_calls,
              COUNT(*) FILTER (WHERE has_game = TRUE)                         AS hits,
              COUNT(*) FILTER (WHERE ended_at IS NULL
                                 AND received_at > NOW() - INTERVAL '15 min') AS in_flight,
              COUNT(*) FILTER (WHERE received_at > NOW() - INTERVAL '24 hours') AS calls_today
            FROM vapi_calls
        """)
    return {
        "total_calls": int(row["total_calls"] or 0),
        "hits":        int(row["hits"] or 0),
        "in_flight":   int(row["in_flight"] or 0),
        "calls_today": int(row["calls_today"] or 0),
    }


@router.get("/retailers")
async def vapi_retailers(
    _user: dict = Depends(require_admin),
    state: Optional[str] = Query(None, description="2-letter state code"),
    search: Optional[str] = Query(None, description="Match name or city (ILIKE)"),
    limit: int = Query(200, ge=1, le=1000),
    only_with_phone: bool = Query(True),
):
    clauses = ["is_active = TRUE"]
    args: list = []
    if state:
        args.append(state.upper().strip())
        clauses.append(f"state_code = ${len(args)}")
    if only_with_phone:
        clauses.append("phone IS NOT NULL AND phone <> ''")
    if search:
        args.append(f"%{search.strip()}%")
        clauses.append(f"(name ILIKE ${len(args)} OR city ILIKE ${len(args)})")
    args.append(limit)
    sql = f"""
        SELECT id, state_code, external_id, name, city, address, zip_code, phone
        FROM state_retailers
        WHERE {' AND '.join(clauses)}
        ORDER BY state_code, city NULLS LAST, name
        LIMIT ${len(args)}
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return {"retailers": [dict(r) for r in rows], "count": len(rows)}


@router.get("/states")
async def vapi_states(_user: dict = Depends(require_admin)):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT state_code,
                   COUNT(*) FILTER (WHERE phone IS NOT NULL AND phone <> '') AS with_phone,
                   COUNT(*)                                                  AS total
            FROM state_retailers
            WHERE is_active = TRUE
            GROUP BY state_code
            HAVING COUNT(*) FILTER (WHERE phone IS NOT NULL AND phone <> '') > 0
            ORDER BY state_code
        """)
    return {"states": [dict(r) for r in rows]}


class DispatchBody(BaseModel):
    retailer_ids: list[int] = Field(..., min_length=1, max_length=MAX_BATCH)
    game_name: str
    game_price: Optional[float] = None
    game_number: Optional[str] = None
    dry_run: bool = False


@router.post("/dispatch")
async def vapi_dispatch(body: DispatchBody, _user: dict = Depends(require_admin)):
    """Ad-hoc dispatch by explicit retailer_ids (used by checkbox-list UIs)."""
    env = _vapi_env()
    if not body.dry_run and not all(env.values()):
        missing = [k for k, v in env.items() if not v]
        raise HTTPException(status_code=400, detail=f"VAPI not configured — missing env: {', '.join(missing)}")

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT external_id, state_code, name, city, phone
               FROM state_retailers
               WHERE id = ANY($1::int[]) AND is_active = TRUE""",
            body.retailer_ids,
        )
    if not rows:
        raise HTTPException(status_code=404, detail="No matching retailers found")

    targets = [dict(r) for r in rows]
    if body.dry_run:
        return {
            "dry_run": True,
            "would_call": sum(1 for t in targets if _to_e164(t.get("phone"))),
            "preview": targets[:25],
        }

    results, skipped = await _dispatch_calls(targets, body.game_name, body.game_price, body.game_number, env)
    success = sum(1 for r in results if r["ok"])
    return {
        "dispatched": success,
        "failed":     len(results) - success,
        "skipped":    skipped,
        "results":    results,
    }


class CampaignBody(BaseModel):
    state: str = Field(..., min_length=2, max_length=2)
    game_name: str
    game_price: Optional[float] = None
    game_number: Optional[str] = None
    max_stores: int = Field(default=100, ge=1, le=MAX_BATCH)
    dry_run: bool = False


@router.post("/dispatch_campaign")
async def vapi_dispatch_campaign(body: CampaignBody, _user: dict = Depends(require_admin)):
    """Campaign-style dispatch: pick top-N retailers for a state and fire."""
    env = _vapi_env()
    if not body.dry_run and not all(env.values()):
        missing = [k for k, v in env.items() if not v]
        raise HTTPException(status_code=400, detail=f"VAPI not configured — missing env: {', '.join(missing)}")

    targets = await _select_scored_retailers(body.state.upper(), body.max_stores)
    if not targets:
        raise HTTPException(status_code=404, detail=f"No callable retailers found for {body.state}")

    if body.dry_run:
        preview = [{"name": t["name"], "city": t["city"], "score": t.get("score"), "phone": t["phone"]} for t in targets[:25]]
        return {
            "dry_run":    True,
            "selected":   len(targets),
            "would_call": sum(1 for t in targets if _to_e164(t.get("phone"))),
            "preview":    preview,
        }

    results, skipped = await _dispatch_calls(targets, body.game_name, body.game_price, body.game_number, env)
    success = sum(1 for r in results if r["ok"])
    return {
        "selected":   len(targets),
        "dispatched": success,
        "failed":     len(results) - success,
        "skipped":    skipped,
        "results":    results[:50],
    }


class TestCallBody(BaseModel):
    phone: str
    game_name: str = "Test Game"
    game_price: Optional[float] = None
    game_number: Optional[str] = None


@router.post("/test_call")
async def vapi_test_call(body: TestCallBody, _user: dict = Depends(require_admin)):
    """Dispatch a single test call to an arbitrary number."""
    env = _vapi_env()
    if not all(env.values()):
        missing = [k for k, v in env.items() if not v]
        raise HTTPException(status_code=400, detail=f"VAPI not configured — missing env: {', '.join(missing)}")

    e164 = _to_e164(body.phone)
    if not e164:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    target = {
        "external_id": "test",
        "state_code":  "",
        "name":        "Test Call",
        "city":        "",
        "phone":       e164,
    }
    results, _ = await _dispatch_calls([target], body.game_name, body.game_price, body.game_number, env)
    r = results[0] if results else {"ok": False, "error": "no result"}
    if not r["ok"]:
        raise HTTPException(status_code=502, detail=f"VAPI dispatch failed: {r.get('error')}")
    return {"ok": True, "call_id": r["call_id"]}
