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

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from backend.database import add_inventory_report, get_pool
from backend.users import require_admin
from backend.caller.vapi_db import (
    delete_vapi_call,
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


# VAPI signals voicemail through endedReason. Different VAPI versions/configs
# emit slightly different strings, so we match anything containing "voicemail".
# We also keep a transcript heuristic as a safety net for cases where VAPI's
# detection didn't fire but the customer side is clearly a voicemail greeting
# (e.g. "if you record your name and reason for calling…").
_VOICEMAIL_TRANSCRIPT_PATTERNS = (
    "leave a message",
    "leave your message",
    "after the tone",
    "at the tone",
    "after the beep",
    "you've reached the voicemail",
    "you have reached the voicemail",
    "record your name and reason",
    "is not available",
    "is unavailable",
    "please record your message",
    "google voice",
)


def _detect_voicemail(ended_reason: Optional[str], transcript: Optional[str]) -> bool:
    er = (ended_reason or "").lower()
    if "voicemail" in er:
        return True
    if not transcript:
        return False
    # Only look at the customer side of the transcript so the assistant's own
    # greeting can't accidentally trigger detection.
    customer_lines = []
    for line in str(transcript).splitlines():
        m = re.match(r"^(user|customer|caller)\s*:\s*(.*)$", line.strip(), re.IGNORECASE)
        if m:
            customer_lines.append(m.group(2).lower())
    text = " ".join(customer_lines) if customer_lines else str(transcript).lower()
    return any(p in text for p in _VOICEMAIL_TRANSCRIPT_PATTERNS)


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

    ended_reason = _pick(msg, "endedReason", "ended_reason")
    is_voicemail = _detect_voicemail(ended_reason, transcript)

    return {
        "vapi_call_id":         call.get("id") or msg.get("callId"),
        "started_at":           started_at,
        "ended_at":             ended_at,
        "duration_sec":         duration,
        "ended_reason":         ended_reason,
        "is_voicemail":         is_voicemail,
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
        "per_ticket":           per_ticket,
        "per_ticket_results":   per_ticket,
        "raw_payload":          payload,
    }


def _verify_secret(provided: Optional[str]) -> None:
    expected = os.getenv("VAPI_WEBHOOK_SECRET")
    if not expected:
        # Fail closed in production. To run unauthenticated locally, set VAPI_VERIFY_OFF=1.
        if os.getenv("VAPI_VERIFY_OFF") == "1":
            return
        raise HTTPException(status_code=503, detail="VAPI_WEBHOOK_SECRET not configured")
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

    inventory_rows_written = 0
    is_test = parsed["retailer_external_id"] == "test"
    has_retailer = bool(parsed["retailer_external_id"]) and not is_test

    if parsed.get("is_voicemail"):
        logger.info(
            "VAPI call %s detected as voicemail (ended_reason=%s) — skipping inventory mirror",
            parsed["vapi_call_id"], parsed.get("ended_reason"),
        )
    elif is_test:
        logger.info("VAPI test call %s — skipping inventory mirror", parsed["vapi_call_id"])
    elif has_retailer:
        match_geo = await find_retailer_by_phone(_digits_only(parsed["to_phone"]))
        lat = match_geo["latitude"]  if match_geo else None
        lng = match_geo["longitude"] if match_geo else None

        # Per-ticket path: write one inventory_reports row per ticket the
        # assistant got an answer on. This is what the public map and retailer
        # inventory views read.
        if parsed["per_ticket"]:
            async with get_pool().acquire() as conn:
                for t in parsed["per_ticket"]:
                    if not isinstance(t, dict):
                        continue
                    t_name = (t.get("name") or "").strip()
                    t_has  = _to_bool(t.get("has_game"))
                    if not t_name or t_has is None:
                        continue
                    t_price = _to_float(t.get("price"))
                    t_conf  = _to_float(t.get("confidence"))
                    t_notes = t.get("notes") or t.get("note") or None
                    note_parts = []
                    if t_notes:
                        note_parts.append(str(t_notes))
                    if t_conf is not None:
                        note_parts.append(f"conf={t_conf:.2f}")
                    await add_inventory_report(
                        conn,
                        retailer_id=parsed["retailer_external_id"],
                        retailer_name=parsed["retailer_name"],
                        retailer_city=parsed["retailer_city"],
                        lat=lat,
                        lng=lng,
                        game_name=t_name,
                        game_price=t_price,
                        has_stock=bool(t_has),
                        source="vapi_call",
                        reporter_username="vapi",
                        notes=" · ".join(note_parts) or None,
                        reported_at=parsed["ended_at"],
                    )
                    inventory_rows_written += 1
            logger.info(
                "VAPI per-ticket inventory mirror: %d rows for %s (call=%s)",
                inventory_rows_written, parsed["retailer_external_id"], parsed["vapi_call_id"],
            )

        # Legacy single-game path — only if no per-ticket array AND we still
        # have a single has_game + game_name (back-compat for older assistants)
        elif parsed["has_game"] is not None and parsed["game_name"]:
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
            inventory_rows_written = 1
            logger.info(
                "VAPI legacy inventory mirror: %s @ %s -> has_stock=%s",
                parsed["game_name"], parsed["retailer_external_id"], parsed["has_game"],
            )
        else:
            logger.info(
                "VAPI call %s stored (no inventory mirror: per_ticket=%s, has_game=%s, game=%s)",
                parsed["vapi_call_id"], bool(parsed["per_ticket"]),
                parsed["has_game"], parsed["game_name"],
            )
    else:
        logger.info(
            "VAPI call %s stored (no retailer external id, can't mirror)",
            parsed["vapi_call_id"],
        )

    return {"ok": True, "call_id": call_id, "inventory_rows_written": inventory_rows_written}


@router.get("/recent")
async def vapi_recent(limit: int = 50, _user: dict = Depends(require_admin)):
    import json as _json
    limit = max(1, min(limit, 500))
    calls = await recent_vapi_calls(limit=limit)
    for c in calls:
        for k in ("received_at", "ended_at"):
            if c.get(k):
                c[k] = c[k].isoformat()
        # asyncpg returns JSONB as str unless a codec is registered — deserialize here.
        pt = c.get("per_ticket_results")
        if isinstance(pt, str):
            try:
                c["per_ticket_results"] = _json.loads(pt)
            except Exception:
                c["per_ticket_results"] = None
    return {"calls": calls, "count": len(calls)}


@router.delete("/calls/{call_id}")
async def vapi_delete_call(call_id: int, _user: dict = Depends(require_admin)):
    deleted = await delete_vapi_call(call_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"ok": True, "deleted_id": call_id}
