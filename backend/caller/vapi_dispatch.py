"""
Outbound VAPI dispatch — admin-only.

Picks retailers from `state_retailers` and creates outbound calls via VAPI's
REST API. Per-call context (store id/name, game info) is passed as
`assistantOverrides.variableValues` so the same VAPI assistant can be used
for every call; the prompt should reference `{{store_name}}`, `{{game_name}}`,
`{{game_price}}`, etc.

Environment:
  VAPI_PRIVATE_KEY      Bearer token for api.vapi.ai (server-side key)
  VAPI_ASSISTANT_ID     Assistant to run on each call
  VAPI_PHONE_NUMBER_ID  VAPI-managed number to dial from

The inbound webhook (vapi_webhook.py) already reads these same variableValues
back out of the end-of-call report and writes inventory_reports — no extra
plumbing needed.
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.database import get_pool
from backend.users import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vapi", tags=["vapi"])

VAPI_API_BASE = "https://api.vapi.ai"
MAX_BATCH = 200
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


@router.get("/config")
async def vapi_config(_user: dict = Depends(require_admin)):
    env = _vapi_env()
    return {
        "configured":         all(env.values()),
        "has_private_key":    bool(env["private_key"]),
        "has_assistant_id":   bool(env["assistant_id"]),
        "has_phone_number":   bool(env["phone_number_id"]),
        "assistant_id":       env["assistant_id"],
        "phone_number_id":    env["phone_number_id"],
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
    """Distinct state codes that have at least one callable retailer."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT state_code,
                   COUNT(*) FILTER (WHERE phone IS NOT NULL AND phone <> '') AS with_phone,
                   COUNT(*) AS total
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
    env = _vapi_env()
    if not body.dry_run and not all(env.values()):
        missing = [k for k, v in env.items() if not v]
        raise HTTPException(
            status_code=400,
            detail=f"VAPI not configured — missing env: {', '.join(missing)}",
        )

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, state_code, external_id, name, city, phone
               FROM state_retailers
               WHERE id = ANY($1::int[]) AND is_active = TRUE""",
            body.retailer_ids,
        )

    if not rows:
        raise HTTPException(status_code=404, detail="No matching retailers found")

    targets = []
    skipped = []
    for r in rows:
        e164 = _to_e164(r["phone"])
        if not e164:
            skipped.append({"id": r["id"], "name": r["name"], "reason": "no valid phone"})
            continue
        targets.append({
            "id":          r["id"],
            "external_id": r["external_id"],
            "state_code":  r["state_code"],
            "name":        r["name"],
            "city":        r["city"],
            "phone_e164":  e164,
        })

    if body.dry_run:
        return {
            "dry_run": True,
            "would_call": len(targets),
            "skipped": skipped,
            "preview": targets[:25],
        }

    sem = asyncio.Semaphore(CONCURRENCY)
    results: list[dict] = []
    async with httpx.AsyncClient(
        base_url=VAPI_API_BASE,
        timeout=30.0,
        headers={
            "Authorization": f"Bearer {env['private_key']}",
            "Content-Type": "application/json",
        },
    ) as client:

        async def _dispatch_one(t: dict):
            payload = {
                "assistantId":    env["assistant_id"],
                "phoneNumberId":  env["phone_number_id"],
                "customer":       {"number": t["phone_e164"]},
                "assistantOverrides": {
                    "variableValues": {
                        "store_id":     t["external_id"],
                        "store_name":   t["name"] or "",
                        "store_city":   t["city"] or "",
                        "store_phone":  t["phone_e164"],
                        "state_code":   t["state_code"],
                        "game_name":    body.game_name,
                        "game_price":   body.game_price if body.game_price is not None else "",
                        "game_number":  body.game_number or "",
                    },
                },
            }
            async with sem:
                try:
                    resp = await client.post("/call", json=payload)
                    ok = 200 <= resp.status_code < 300
                    data = {}
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"text": resp.text[:300]}
                    results.append({
                        "retailer_id": t["id"],
                        "name":        t["name"],
                        "ok":          ok,
                        "status":      resp.status_code,
                        "call_id":     data.get("id") if ok else None,
                        "error":       None if ok else (data.get("message") or data.get("text") or "unknown"),
                    })
                except Exception as exc:
                    logger.exception("VAPI dispatch failed for retailer %s", t["id"])
                    results.append({
                        "retailer_id": t["id"],
                        "name":        t["name"],
                        "ok":          False,
                        "status":      0,
                        "call_id":     None,
                        "error":       str(exc),
                    })

        await asyncio.gather(*(_dispatch_one(t) for t in targets))

    success = sum(1 for r in results if r["ok"])
    return {
        "dispatched": success,
        "failed":     len(results) - success,
        "skipped":    skipped,
        "results":    results,
    }
