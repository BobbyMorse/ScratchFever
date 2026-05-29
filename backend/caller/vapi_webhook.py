"""
VAPI end-of-call webhook.

VAPI handles the call (telephony, STT, LLM, TTS) and POSTs a webhook back
when the call ends. We persist the result and mirror inventory findings
to the public `inventory_reports` table.

Config:
  VAPI_WEBHOOK_SECRET  Optional. If set, requests must include a matching
                       X-VAPI-Secret header (case-insensitive). VAPI lets
                       you set a fixed secret per assistant.

Expected payload (VAPI server-message, type="end-of-call-report"):
  {
    "message": {
      "type": "end-of-call-report",
      "call": { "id": "...", "createdAt": "...", "customer": {"number": "+1..."} },
      "transcript": "...",
      "summary": "...",
      "analysis": { "structuredData": {...}, "summary": "..." },
      "endedReason": "...",
      "durationSeconds": 42.5,
      "assistant": { "variableValues": {...} },
      "startedAt": "...",
      "endedAt": "..."
    }
  }

Field locations vary across VAPI SDK versions, so we extract defensively
and store the raw payload as JSONB for inspection if anything is missing.
"""
from __future__ import annotations
import datetime as dt
import hmac
import logging
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from backend.database import add_inventory_report, get_pool
from backend.caller.vapi_db import (
    find_retailer_by_phone,
    insert_vapi_call,
    recent_vapi_calls,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vapi", tags=["vapi"])


def _digits_only(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def _parse_dt(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, (int, float)):
        # epoch seconds or millis
        if value > 1e12:
            value = value / 1000.0
        return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    if isinstance(value, str):
        s = value.replace("Z", "+00:00")
        try:
            return dt.datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "y", "1", "in stock", "has", "have"}:
            return True
        if v in {"false", "no", "n", "0", "out of stock", "out", "none"}:
            return False
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick(d: dict, *keys: str) -> Any:
    """Return the first non-None value among the given keys (case-insensitive)."""
    if not isinstance(d, dict):
        return None
    lower = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v is not None:
            return v
    return None


def _extract(payload: dict) -> dict:
    """Pull a flat dict out of VAPI's nested webhook shape."""
    msg = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(msg, dict):
        msg = payload if isinstance(payload, dict) else {}

    call = msg.get("call") or {}
    analysis = msg.get("analysis") or {}
    structured = analysis.get("structuredData") or analysis.get("structured_data") or {}

    # Variables we pass into VAPI per-call (game info, store id, state, etc.)
    variables = (
        (msg.get("assistant") or {}).get("variableValues")
        or (msg.get("assistantOverrides") or {}).get("variableValues")
        or (call.get("assistantOverrides") or {}).get("variableValues")
        or msg.get("variableValues")
        or {}
    )

    customer = call.get("customer") or msg.get("customer") or {}
    phone_number = call.get("phoneNumber") or {}

    to_phone = _pick(customer, "number", "phone") or _pick(variables, "store_phone", "to_phone", "phone")
    from_phone = _pick(phone_number, "number") or _pick(variables, "from_phone")

    started_at = _parse_dt(_pick(msg, "startedAt", "started_at") or call.get("createdAt"))
    ended_at = _parse_dt(_pick(msg, "endedAt", "ended_at"))
    duration = _to_float(_pick(msg, "durationSeconds", "duration_seconds", "durationSec", "duration"))

    # Per-ticket array (new schema) — list of {name, price, has_game, confidence, notes}
    per_ticket = _pick(structured, "per_ticket_results", "perTicketResults", "tickets", "results")
    if not isinstance(per_ticket, list):
        per_ticket = None

    # Legacy single-game fields still tolerated for assistants that haven't moved to per-ticket
    has_game = _to_bool(_pick(structured, "has_game", "hasGame", "in_stock", "inStock"))
    confidence = _to_float(_pick(structured, "confidence"))
    can_order = _to_bool(_pick(structured, "can_order", "canOrder"))
    extracted_notes = _pick(structured, "notes", "note", "details") or _pick(structured, "summary_notes")

    # Roll the per-ticket list up into the single legacy columns so the dashboard
    # still has useful values for in-flight → final transition: has_game = ANY,
    # confidence = MAX.
    if per_ticket:
        any_yes = any(_to_bool(t.get("has_game")) is True for t in per_ticket if isinstance(t, dict))
        any_no  = any(_to_bool(t.get("has_game")) is False for t in per_ticket if isinstance(t, dict))
        if has_game is None:
            has_game = True if any_yes else (False if any_no else None)
        confs = [_to_float(t.get("confidence")) for t in per_ticket if isinstance(t, dict)]
        confs = [c for c in confs if c is not None]
        if confidence is None and confs:
            confidence = max(confs)

    summary = msg.get("summary") or analysis.get("summary")
    transcript = msg.get("transcript") or analysis.get("transcript")

    return {
        "vapi_call_id":         call.get("id") or msg.get("callId"),
        "started_at":           started_at,
        "ended_at":             ended_at,
        "duration_sec":         duration,
        "ended_reason":         _pick(msg, "endedReason", "ended_reason"),
        "to_phone":             to_phone,
        "from_phone":           from_phone,
        "state_code":           (_pick(variables, "state_code", "stateCode", "state") or "").upper() or None,
        "retailer_external_id": _pick(variables, "store_id", "retailer_id", "external_id"),
        "retailer_name":        _pick(variables, "store_name", "retailer_name"),
        "retailer_city":        _pick(variables, "store_city", "retailer_city", "city"),
        "game_name":            _pick(variables, "game_name", "gameName"),
        "game_price":           _to_float(_pick(variables, "game_price", "gamePrice", "price")),
        "game_number":          _pick(variables, "game_number", "gameNumber"),
        "has_game":             has_game,
        "confidence":           confidence,
        "can_order":            can_order,
        "summary":              summary,
        "notes":                extracted_notes,
        "transcript":           transcript,
        "raw_payload":          payload,
    }


def _verify_secret(provided: Optional[str]) -> None:
    expected = os.getenv("VAPI_WEBHOOK_SECRET")
    if not expected:
        return  # secret not configured -> dev mode, accept everything
    if not provided or not hmac.compare_digest(provided.strip(), expected.strip()):
        raise HTTPException(status_code=401, detail="bad signature")


@router.post("/webhook")
async def vapi_webhook(
    request: Request,
    x_vapi_secret: Optional[str] = Header(default=None),
    x_vapi_signature: Optional[str] = Header(default=None),
):
    # Accept either header name; VAPI assistants can be configured to send either.
    _verify_secret(x_vapi_secret or x_vapi_signature)

    payload = await request.json()

    msg_type = ""
    if isinstance(payload, dict):
        msg_type = (payload.get("message") or {}).get("type") or payload.get("type") or ""

    # Only the end-of-call report carries the transcript + structured data.
    # Other server-messages (status updates, transcripts mid-call, etc.) get
    # acknowledged with 200 but no DB write.
    if msg_type and msg_type != "end-of-call-report":
        logger.info("VAPI webhook ignored (type=%s)", msg_type)
        return {"ok": True, "ignored": msg_type}

    parsed = _extract(payload)

    # If VAPI variables didn't carry the store identity, try to match by phone.
    if not parsed["retailer_external_id"] and parsed["to_phone"]:
        match = await find_retailer_by_phone(_digits_only(parsed["to_phone"]))
        if match:
            parsed["retailer_external_id"] = match["external_id"]
            parsed["retailer_name"]        = parsed["retailer_name"]  or match["name"]
            parsed["retailer_city"]        = parsed["retailer_city"]  or match["city"]
            parsed["state_code"]           = parsed["state_code"]     or match["state_code"]

    call_id = await insert_vapi_call(parsed)

    inventory_id = None
    is_test = isinstance(parsed["retailer_external_id"], str) and parsed["retailer_external_id"].startswith("test")
    if is_test:
        logger.info("VAPI test call %s — skipping inventory mirror", parsed["vapi_call_id"])
    elif parsed["has_game"] is not None and parsed["retailer_external_id"] and parsed["game_name"]:
        match_geo = await find_retailer_by_phone(_digits_only(parsed["to_phone"]))
        lat = match_geo["latitude"]  if match_geo else None
        lng = match_geo["longitude"] if match_geo else None
        async with get_pool().acquire() as conn:
            await add_inventory_report(
                conn,
                retailer_id=parsed["retailer_external_id"],
                retailer_name=parsed["retailer_name"],
                retailer_city=parsed["retailer_city"],
                lat=lat,
                lng=lng,
                game_name=parsed["game_name"],
                game_price=parsed["game_price"],
                has_stock=bool(parsed["has_game"]),
                source="vapi_call",
                reporter_username="vapi",
                notes=(parsed["notes"] or parsed["summary"] or None),
                reported_at=parsed["ended_at"],
            )
        inventory_id = "written"
        logger.info(
            "VAPI inventory mirror: %s @ %s -> has_stock=%s conf=%s",
            parsed["game_name"], parsed["retailer_external_id"],
            parsed["has_game"], parsed["confidence"],
        )
    else:
        logger.info(
            "VAPI call %s stored (no inventory mirror: has_game=%s, retailer=%s, game=%s)",
            parsed["vapi_call_id"], parsed["has_game"],
            parsed["retailer_external_id"], parsed["game_name"],
        )

    return {"ok": True, "call_id": call_id, "inventory_report": inventory_id}


@router.get("/recent")
async def vapi_recent(limit: int = 50):
    limit = max(1, min(limit, 500))
    calls = await recent_vapi_calls(limit=limit)
    for c in calls:
        for k in ("received_at", "ended_at"):
            if c.get(k):
                c[k] = c[k].isoformat()
    return {"calls": calls, "count": len(calls)}
