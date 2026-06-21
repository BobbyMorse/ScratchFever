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
import asyncio
import datetime as dt
import hmac
import json
import logging
import os
import re
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from backend.database import add_inventory_report, get_pool
from backend.users import require_admin
from backend.caller.vapi_db import (
    delete_vapi_call,
    find_retailer_by_phone,
    insert_vapi_call,
    recent_vapi_calls,
)
from backend.caller.vapi_dispatch import record_transport_error
from backend.caller.analysis_fallback import extract_from_transcript

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


def _split_asked_names(asked_field: Optional[str]) -> list[str]:
    """vapi_calls.game_name stores the dispatched ticket list as a comma-
    separated label ('300X, Fabulous Fortune'). Split it back into a list."""
    if not asked_field:
        return []
    return [n.strip() for n in str(asked_field).split(",") if n.strip()]


def _canonical_ticket_name(reported: str, asked: list[str]) -> str:
    """Map the assistant-reported ticket name (which can drift in case/spacing
    — '300 x' for '300X', 'fabulous fortune' for 'Fabulous Fortune') back to
    the canonical name we actually asked about. Falls back to the reported
    name when no match — bot occasionally invents extras."""
    if not reported or not asked:
        return reported
    target = re.sub(r"\s+", "", reported).lower()
    for canonical in asked:
        if re.sub(r"\s+", "", canonical).lower() == target:
            return canonical
    return reported


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


def _transcript_from_messages(messages: list) -> str:
    """Reconstruct a readable transcript from VAPI's messages[] array.
    Some VAPI assistant configs send messages[] but not the pre-rendered
    `transcript` string; if we only check transcript we'd see nothing
    and Haiku would have nothing to extract from. Returns "" when the
    list is empty / malformed."""
    if not isinstance(messages, list):
        return ""
    lines = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").lower()
        text = m.get("message") or m.get("content") or ""
        if not text or not isinstance(text, str):
            continue
        # VAPI uses "bot"/"assistant" interchangeably for the AI side.
        if role in ("bot", "assistant"):
            speaker = "AI"
        elif role in ("user", "customer"):
            speaker = "User"
        elif role == "system":
            continue  # don't include system prompts in transcript for extraction
        else:
            speaker = role or "?"
        lines.append(f"{speaker}: {text.strip()}")
    return "\n".join(lines)


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
    transcript = (
        msg.get("transcript")
        or analysis.get("transcript")
        or _transcript_from_messages(msg.get("messages") or call.get("messages") or [])
    )

    # Funnel / disposition fields from the structured-data extractor.
    answered_phone             = _to_bool(_pick(structured, "answered_phone", "answeredPhone"))
    confirmed_sells_scratch    = _to_bool(_pick(structured, "confirmed_sells_scratch", "confirmedSellsScratch"))
    inventory_actually_checked = _to_bool(_pick(structured, "inventory_actually_checked", "inventoryActuallyChecked"))
    customer_disposition       = _pick(structured, "customer_disposition", "customerDisposition")
    ended_early_reason         = _pick(structured, "ended_early_reason", "endedEarlyReason")
    def _maybe_int(v):
        try:
            return int(v) if v is not None else None
        except Exception:
            return None
    tickets_asked_count    = _maybe_int(_pick(structured, "tickets_asked_count", "ticketsAskedCount"))
    tickets_answered_count = _maybe_int(_pick(structured, "tickets_answered_count", "ticketsAnsweredCount"))

    ended_reason = _pick(msg, "endedReason", "ended_reason")
    is_voicemail = _detect_voicemail(ended_reason, transcript)

    # Two-party-consent compliance: we use the transcript only transiently
    # (voicemail detection above + structured extraction by VAPI's analysis
    # plan, plus our Haiku fallback below if VAPI's failed). Nothing about
    # the conversation content gets persisted — `transcript` is forced to
    # None in the return dict, and the transient `_transcript_for_extraction`
    # key starts with an underscore so it's never written by insert_vapi_call
    # (which row.get()s only the columns it knows about).

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
        "transcript":           None,
        "per_ticket":           per_ticket,
        "per_ticket_results":   per_ticket,
        "raw_payload":          None,
        "answered_phone":             answered_phone,
        "confirmed_sells_scratch":    confirmed_sells_scratch,
        "inventory_actually_checked": inventory_actually_checked,
        "tickets_asked_count":        tickets_asked_count,
        "tickets_answered_count":     tickets_answered_count,
        "customer_disposition":       customer_disposition,
        "ended_early_reason":         ended_early_reason,
        # Underscore prefix → ignored by insert_vapi_call, never hits the DB.
        # Used in-memory by the Haiku fallback before being discarded with the
        # rest of the parsed dict at the end of webhook processing.
        "_transcript_for_extraction": transcript,
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

    # status-update messages cover the full call lifecycle: queued → ringing →
    # in-progress → forwarding → ended. We persist each tick to `live_status`
    # so the UI can show real-time progress. The ended tick also covers failure
    # modes that don't produce an end-of-call-report (transport errors,
    # did-not-answer, busy) — terminal fields get written then too.
    if msg_type == "status-update":
        m = payload.get("message") or {}
        status = (m.get("status") or "").lower()
        await _persist_live_status(m, status)
        if status == "ended":
            await _persist_terminal_status(m)
            _record_transport_error_from_msg(m)
        return {"ok": True, "kind": "status-update", "status": status}

    # Non-end-of-call messages we don't care about.
    if msg_type and msg_type != "end-of-call-report":
        logger.info("VAPI webhook ignored (type=%s)", msg_type)
        return {"ok": True, "ignored": msg_type}

    parsed = _extract(payload)
    _record_transport_error_from_msg(payload.get("message") or payload)

    # If VAPI variables didn't carry the store identity, try to match by phone.
    if not parsed["retailer_external_id"] and parsed["to_phone"]:
        match = await find_retailer_by_phone(_digits_only(parsed["to_phone"]))
        if match:
            parsed["retailer_external_id"] = match["external_id"]
            parsed["retailer_name"]        = parsed["retailer_name"]  or match["name"]
            parsed["retailer_city"]        = parsed["retailer_city"]  or match["city"]
            parsed["state_code"]           = parsed["state_code"]     or match["state_code"]

    # Haiku fallback: VAPI's analysisPlan flakes silently sometimes, leaving
    # us with a transcript but no structuredData. Rather than depend on a
    # delayed re-fetch that might never get data either, extract on our
    # side from the transcript that's already in the payload. Same JSON
    # shape, so the rest of the pipeline doesn't care which extractor ran.
    await _maybe_apply_haiku_fallback(parsed)

    call_id = await insert_vapi_call(parsed)

    inventory_rows_written = 0
    is_test = parsed["retailer_external_id"] == "test"
    has_retailer = bool(parsed["retailer_external_id"]) and not is_test

    # Idempotency guard: only mirror once per call. The end-of-call-report
    # can be re-delivered by VAPI and the backfill script can also call into
    # this code path; without the guard we'd double-count inventory.
    async with get_pool().acquire() as conn:
        already_mirrored = await conn.fetchval(
            "SELECT inventory_mirrored_at IS NOT NULL FROM vapi_calls WHERE id = $1",
            call_id,
        )
    if already_mirrored:
        return {"ok": True, "call_id": call_id, "inventory_rows_written": 0, "already_mirrored": True}

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
            asked_names = _split_asked_names(parsed.get("game_name"))
            async with get_pool().acquire() as conn:
                for t in parsed["per_ticket"]:
                    if not isinstance(t, dict):
                        continue
                    raw_name = (t.get("name") or "").strip()
                    t_name = _canonical_ticket_name(raw_name, asked_names)
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
                        state_code=parsed["state_code"],
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
                    state_code=parsed["state_code"],
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

    if inventory_rows_written > 0:
        async with get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE vapi_calls SET inventory_mirrored_at = NOW() WHERE id = $1",
                call_id,
            )

    # VAPI computes the analysis block (summary + per_ticket_results) async
    # AFTER firing end-of-call-report, and never re-fires the webhook when it
    # lands. If we got a conversational call but no per_ticket data yet,
    # schedule a delayed re-fetch from /call/{id} so the inventory mirror
    # still happens once VAPI's analysis catches up.
    should_refetch = (
        has_retailer
        and not parsed.get("is_voicemail")
        and inventory_rows_written == 0
        and not parsed["per_ticket"]
        and (parsed.get("duration_sec") or 0) >= 5
        and parsed.get("vapi_call_id")
    )
    if should_refetch:
        asyncio.create_task(
            _refetch_and_apply_analysis(call_id, parsed["vapi_call_id"])
        )

    # Two-party-consent scrub: once we've had time to extract structured data
    # from this call (analysis path or Haiku fallback), delete it from VAPI's
    # side so the transcript artifact stops living on their dashboard. 180s
    # buffer is longer than the 90s re-fetch wait + extraction.
    if parsed.get("vapi_call_id"):
        asyncio.create_task(
            _scrub_vapi_after_delay(parsed["vapi_call_id"], delay_seconds=180.0)
        )

    return {"ok": True, "call_id": call_id, "inventory_rows_written": inventory_rows_written}


async def _scrub_vapi_after_delay(vapi_call_id: str, delay_seconds: float = 180.0) -> None:
    """Fire-and-forget scrub of a VAPI call after we've had time to extract
    everything we need. Removes the transcript artifact from VAPI's side so
    only the structured inventory result persists (in our local DB)."""
    if not vapi_call_id:
        return
    await asyncio.sleep(delay_seconds)
    ok = await _delete_call_on_vapi(vapi_call_id)
    if ok:
        logger.info("VAPI scrub: deleted /call/%s after analysis window", vapi_call_id)


async def _maybe_apply_haiku_fallback(parsed: dict) -> None:
    """If VAPI's structuredData was missing but we have a transcript and a
    real conversation (duration >= 5s, not voicemail), run Claude Haiku on
    our side to produce the same shape VAPI would have. Mutates `parsed`
    in place so the rest of the webhook handler sees the extracted fields
    as if they came from VAPI directly. The transient transcript stays
    in-memory and is never written to the DB."""
    if parsed.get("per_ticket"):
        return  # VAPI's extractor worked — nothing to do
    if parsed.get("is_voicemail"):
        return
    if (parsed.get("duration_sec") or 0) < 5:
        return
    transcript = parsed.get("_transcript_for_extraction")
    if not transcript:
        return
    asked = _split_asked_names(parsed.get("game_name"))
    if not asked:
        return

    extracted = await extract_from_transcript(transcript, asked)
    if not extracted:
        return

    per_ticket = extracted.get("per_ticket_results")
    if isinstance(per_ticket, list) and per_ticket:
        parsed["per_ticket"]         = per_ticket
        parsed["per_ticket_results"] = per_ticket
        # Roll up the same single-column legacy fields the original _extract
        # computes, so dashboards and downstream logic stay consistent.
        any_yes = any(_to_bool(t.get("has_game")) is True for t in per_ticket if isinstance(t, dict))
        any_no  = any(_to_bool(t.get("has_game")) is False for t in per_ticket if isinstance(t, dict))
        if parsed.get("has_game") is None:
            parsed["has_game"] = True if any_yes else (False if any_no else None)
        confs = [_to_float(t.get("confidence")) for t in per_ticket if isinstance(t, dict)]
        confs = [c for c in confs if c is not None]
        if parsed.get("confidence") is None and confs:
            parsed["confidence"] = max(confs)

    # COALESCE-style merge — only fill in fields VAPI didn't already give us.
    def _fill(key: str, value):
        if value is not None and parsed.get(key) in (None, ""):
            parsed[key] = value
    _fill("summary",                    extracted.get("summary"))
    _fill("answered_phone",             _to_bool(extracted.get("answered_phone")))
    _fill("confirmed_sells_scratch",    _to_bool(extracted.get("confirmed_sells_scratch")))
    _fill("inventory_actually_checked", _to_bool(extracted.get("inventory_actually_checked")))
    _fill("customer_disposition",       extracted.get("customer_disposition"))
    _fill("ended_early_reason",         extracted.get("ended_early_reason"))
    parsed["notes"] = (parsed.get("notes") or "haiku_fallback")

    logger.info(
        "Haiku fallback extracted analysis for call %s (per_ticket=%d)",
        parsed.get("vapi_call_id"),
        len(per_ticket) if isinstance(per_ticket, list) else 0,
    )


async def _apply_analysis_for_row(
    local_id: int,
    vapi_call_id: str,
    key: str,
    client: httpx.AsyncClient,
) -> dict:
    """Pull /call/{id} from VAPI and apply whatever analysis is now available
    (summary, per_ticket_results, structured funnel fields, inventory mirror).
    Idempotent: COALESCE-based writes plus inventory_mirrored_at guard. Returns
    a small dict {fetched, analysis_written, inventory_rows} for the caller's
    reporting. Never raises — transport errors get logged and the row stays
    unchanged so a later attempt can succeed."""
    result = {"fetched": False, "analysis_written": False, "inventory_rows": 0}
    try:
        r = await client.get(
            f"https://api.vapi.ai/call/{vapi_call_id}",
            headers={"Authorization": f"Bearer {key}"},
        )
    except Exception as exc:
        logger.warning("VAPI re-fetch /call/%s failed: %s", vapi_call_id, exc)
        return result
    if r.status_code != 200:
        logger.warning("VAPI re-fetch /call/%s -> %s", vapi_call_id, r.status_code)
        return result
    result["fetched"] = True

    d = r.json()
    analysis   = d.get("analysis") or {}
    structured = analysis.get("structuredData") or {}
    summary    = analysis.get("summary")
    per_ticket = structured.get("per_ticket_results")

    # Haiku fallback for the reconcile/poller path: VAPI flaked on this call's
    # analysis but their /call/{id} response still includes the transcript.
    # Run our own Haiku extraction on it so the row recovers instead of
    # sitting empty forever. Loaded only when needed — we can't know the
    # asked tickets without one more DB lookup.
    if not structured:
        transcript = d.get("transcript") or analysis.get("transcript")
        if transcript and (d.get("status") == "ended"):
            pool = get_pool()
            async with pool.acquire() as conn:
                meta = await conn.fetchrow(
                    "SELECT duration_sec, is_voicemail, game_name FROM vapi_calls WHERE id = $1",
                    local_id,
                )
            if (
                meta
                and not meta["is_voicemail"]
                and (meta["duration_sec"] or 0) >= 5
            ):
                asked = _split_asked_names(meta["game_name"])
                if asked:
                    extracted = await extract_from_transcript(transcript, asked)
                    if extracted:
                        structured = extracted
                        per_ticket = extracted.get("per_ticket_results") or per_ticket
                        if not summary:
                            summary = extracted.get("summary")
                        logger.info(
                            "Haiku fallback (reconcile) extracted analysis for /call/%s",
                            vapi_call_id,
                        )

    if not summary and not per_ticket:
        return result

    per_ticket_json = json.dumps(per_ticket, default=str) if per_ticket else None

    def _maybe_int(v):
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
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
            RETURNING retailer_external_id, retailer_name, retailer_city,
                      to_phone, ended_at, is_voicemail, game_name,
                      inventory_mirrored_at IS NOT NULL AS mirrored
            """,
            local_id,
            summary,
            structured.get("summary_notes"),
            per_ticket_json,
            _to_bool(structured.get("answered_phone")),
            _to_bool(structured.get("confirmed_sells_scratch")),
            _to_bool(structured.get("inventory_actually_checked")),
            _maybe_int(structured.get("tickets_asked_count")),
            _maybe_int(structured.get("tickets_answered_count")),
            structured.get("customer_disposition"),
            structured.get("ended_early_reason"),
        )
    result["analysis_written"] = True

    if not row or row["mirrored"] or row["is_voicemail"] or not per_ticket:
        return result
    rext = row["retailer_external_id"]
    if not rext or rext == "test":
        return result

    geo = await find_retailer_by_phone(_digits_only(row["to_phone"] or ""))
    lat = geo["latitude"]  if geo else None
    lng = geo["longitude"] if geo else None
    asked_names = _split_asked_names(row["game_name"])

    rows_written = 0
    async with pool.acquire() as conn:
        for t in per_ticket:
            if not isinstance(t, dict):
                continue
            raw_name = (t.get("name") or "").strip()
            t_name = _canonical_ticket_name(raw_name, asked_names)
            t_has  = _to_bool(t.get("has_game"))
            if not t_name or t_has is None:
                continue
            t_price = _to_float(t.get("price"))
            t_conf  = _to_float(t.get("confidence"))
            t_notes = t.get("notes") or t.get("note")
            note_parts = []
            if t_notes:
                note_parts.append(str(t_notes))
            if t_conf is not None:
                note_parts.append(f"conf={t_conf:.2f}")
            await add_inventory_report(
                conn,
                retailer_id=rext,
                retailer_name=row["retailer_name"],
                retailer_city=row["retailer_city"],
                lat=lat,
                lng=lng,
                game_name=t_name,
                game_price=t_price,
                has_stock=bool(t_has),
                source="vapi_call",
                reporter_username="vapi",
                notes=" · ".join(note_parts) or None,
                reported_at=row["ended_at"],
            )
            rows_written += 1
    if rows_written > 0:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE vapi_calls SET inventory_mirrored_at = NOW() WHERE id = $1",
                local_id,
            )
        logger.info(
            "VAPI re-fetch %s wrote %d inventory rows", vapi_call_id, rows_written,
        )
    result["inventory_rows"] = rows_written
    return result


async def _refetch_and_apply_analysis(local_id: int, vapi_call_id: str) -> None:
    """Fire-and-forget backstop scheduled from the end-of-call-report handler.
    Single 90s wait then one fetch — the durable, multi-attempt path lives in
    /reconcile_inflight, which the dashboard Refresh button calls."""
    key = os.getenv("VAPI_PRIVATE_KEY")
    if not key:
        return
    await asyncio.sleep(90)
    async with httpx.AsyncClient(timeout=15.0) as client:
        outcome = await _apply_analysis_for_row(local_id, vapi_call_id, key, client)
    if not outcome["analysis_written"]:
        logger.info("VAPI delayed re-fetch %s: analysis still empty after 90s", vapi_call_id)


@router.get("/recent")
async def vapi_recent(limit: int = 50, _user: dict = Depends(require_admin)):
    import json as _json
    limit = max(1, min(limit, 10000))
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


async def _delete_call_on_vapi(vapi_call_id: str) -> bool:
    """Best-effort DELETE on VAPI's side so the call (and any artifacts they
    retain) is removed from their dashboard too. Returns True on 2xx/404
    (treat already-gone as success). Never raises — local deletion still
    proceeds even if VAPI's side fails."""
    key = os.getenv("VAPI_PRIVATE_KEY")
    if not key or not vapi_call_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.delete(
                f"https://api.vapi.ai/call/{vapi_call_id}",
                headers={"Authorization": f"Bearer {key}"},
            )
        if r.status_code in (200, 204, 404):
            return True
        logger.warning("VAPI DELETE /call/%s -> %s: %s",
                       vapi_call_id, r.status_code, r.text[:200])
        return False
    except Exception as exc:
        logger.warning("VAPI DELETE /call/%s failed: %s", vapi_call_id, exc)
        return False


@router.delete("/calls/{call_id}")
async def vapi_delete_call(call_id: int, _user: dict = Depends(require_admin)):
    pool = get_pool()
    async with pool.acquire() as conn:
        vapi_call_id = await conn.fetchval(
            "SELECT vapi_call_id FROM vapi_calls WHERE id = $1", call_id
        )
    if vapi_call_id:
        await _delete_call_on_vapi(vapi_call_id)
    deleted = await delete_vapi_call(call_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"ok": True, "deleted_id": call_id}


@router.post("/calls/{call_id}/refetch")
async def vapi_force_refetch(call_id: int, _user: dict = Depends(require_admin)):
    """Force-pull /call/{id} from VAPI for one local row, apply any analysis,
    return the raw VAPI response. Diagnostic — lets you see exactly what VAPI
    has for a stuck row without guessing."""
    key = os.getenv("VAPI_PRIVATE_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="VAPI_PRIVATE_KEY not configured")
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, vapi_call_id FROM vapi_calls WHERE id = $1", call_id
        )
    if not row or not row["vapi_call_id"]:
        raise HTTPException(status_code=404, detail="Call not found or has no vapi_call_id")

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            raw = await client.get(
                f"https://api.vapi.ai/call/{row['vapi_call_id']}",
                headers={"Authorization": f"Bearer {key}"},
            )
        except Exception as exc:
            return {"ok": False, "error": f"VAPI GET failed: {exc}"}
        raw_data = raw.json() if raw.status_code == 200 else None
        outcome = await _apply_analysis_for_row(
            row["id"], row["vapi_call_id"], key, client,
        )

    analysis = (raw_data or {}).get("analysis") or {}
    structured = analysis.get("structuredData") or {}
    return {
        "ok":                  True,
        "vapi_call_id":        row["vapi_call_id"],
        "vapi_status_code":    raw.status_code,
        "vapi_call_status":    (raw_data or {}).get("status"),
        "vapi_ended_reason":   (raw_data or {}).get("endedReason"),
        "has_summary":         bool(analysis.get("summary")),
        "has_structured_data": bool(structured),
        "has_per_ticket":      bool(structured.get("per_ticket_results")),
        "analysis_keys":       sorted(list(analysis.keys())),
        "structured_keys":     sorted(list(structured.keys())),
        "summary_preview":     (analysis.get("summary") or "")[:200],
        "per_ticket":          structured.get("per_ticket_results"),
        "applied":             outcome,
    }


def _parse_vapi_iso(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _record_transport_error_from_msg(msg: dict) -> None:
    """Feed VAPI's endedReason + phoneNumberId into the dispatcher's circuit
    breaker. No-op when either is missing or when the reason isn't a transport
    failure."""
    if not isinstance(msg, dict):
        return
    call = msg.get("call") or {}
    pid = (
        call.get("phoneNumberId")
        or (call.get("phoneNumber") or {}).get("id")
        or msg.get("phoneNumberId")
    )
    ended_reason = _pick(msg, "endedReason", "ended_reason")
    record_transport_error(pid, ended_reason)


async def _persist_live_status(msg: dict, status: str) -> None:
    """Write the current VAPI lifecycle status (queued/ringing/in-progress/
    forwarding/ended) to the row so the UI can show real-time progress."""
    call = msg.get("call") or {}
    vapi_call_id = call.get("id") or msg.get("callId")
    if not vapi_call_id or not status:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE vapi_calls SET live_status = $2 WHERE vapi_call_id = $1",
            vapi_call_id, status,
        )


async def _persist_terminal_status(msg: dict) -> None:
    """Update only the terminal-state fields (ended_at, ended_reason, duration)
    when VAPI sends a status-update with status=ended. Doesn't touch transcript,
    summary, or per_ticket_results — those come in the separate end-of-call-report
    if they exist. Idempotent: only fills in NULLs."""
    call = msg.get("call") or {}
    vapi_call_id = call.get("id") or msg.get("callId")
    if not vapi_call_id:
        return
    ended_reason = _pick(msg, "endedReason", "ended_reason")
    started = _parse_vapi_iso(_pick(msg, "startedAt", "started_at"))
    ended   = _parse_vapi_iso(_pick(msg, "endedAt",   "ended_at"))
    duration = _to_float(_pick(msg, "durationSeconds", "duration_sec", "duration"))
    if duration is None and started and ended:
        duration = (ended - started).total_seconds()

    pool = get_pool()
    async with pool.acquire() as conn:
        # If a row exists, fill in any NULL terminal fields. If not, leave it —
        # the dispatcher placeholder + end-of-call-report path will create it.
        await conn.execute(
            """
            UPDATE vapi_calls SET
                started_at   = COALESCE(started_at,   $2),
                ended_at     = COALESCE(ended_at,     $3),
                duration_sec = COALESCE(duration_sec, $4),
                ended_reason = COALESCE(ended_reason, $5)
            WHERE vapi_call_id = $1
            """,
            vapi_call_id, started, ended, duration, ended_reason,
        )


@router.post("/reconcile_inflight")
async def vapi_reconcile_inflight(_user: dict = Depends(require_admin)):
    """Heal local rows by pulling fresh state from VAPI. Two passes:
      1. In-flight rows (no ended_reason yet) — typically transport-error /
         no-answer / voicemail-without-leave calls that never fire an
         end-of-call-report webhook.
      2. Analysis-pending rows (have ended_reason but missing summary or
         per_ticket_results) — VAPI computes structuredData asynchronously
         after end-of-call-report and never re-fires the webhook when it
         lands, so without this pass the summary and the public inventory
         mirror stay empty forever.
    Both passes share one HTTP client and write idempotently."""
    key = os.getenv("VAPI_PRIVATE_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="VAPI_PRIVATE_KEY not configured")

    pool = get_pool()
    async with pool.acquire() as conn:
        inflight = await conn.fetch(
            """
            SELECT id, vapi_call_id
            FROM vapi_calls
            WHERE vapi_call_id IS NOT NULL
              AND ended_at IS NULL
              AND ended_reason IS NULL
              AND received_at > NOW() - INTERVAL '7 days'
            ORDER BY received_at DESC
            LIMIT 200
            """
        )
        # Analysis-pending: terminal-state rows that the webhook handler's
        # 90s backstop either gave up on, missed (process restart), or that
        # VAPI's extractor took longer than 90s to populate. duration_sec >= 5
        # is what the webhook itself uses to decide whether to schedule the
        # backstop — anything shorter is no-answer / failed-transport / silence
        # and definitionally has no conversation for VAPI to extract, so it
        # would be wasted API calls.
        analysis_pending = await conn.fetch(
            """
            SELECT id, vapi_call_id
            FROM vapi_calls
            WHERE vapi_call_id IS NOT NULL
              AND ended_reason IS NOT NULL
              AND COALESCE(is_voicemail, false) = false
              AND duration_sec IS NOT NULL
              AND duration_sec >= 5
              AND (summary IS NULL OR per_ticket_results IS NULL)
              AND inventory_mirrored_at IS NULL
              AND received_at < NOW() - INTERVAL '90 seconds'
              AND received_at > NOW() - INTERVAL '2 hours'
            ORDER BY received_at DESC
            LIMIT 200
            """
        )

    updated = 0
    still_pending = 0
    analysis_filled = 0
    inventory_rows = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for r in inflight:
            try:
                resp = await client.get(
                    f"https://api.vapi.ai/call/{r['vapi_call_id']}",
                    headers={"Authorization": f"Bearer {key}"},
                )
            except Exception as exc:
                logger.warning("reconcile GET /call/%s failed: %s", r["vapi_call_id"], exc)
                continue
            if resp.status_code != 200:
                continue
            d = resp.json()
            if d.get("status") != "ended":
                still_pending += 1
                continue

            started = _parse_vapi_iso(d.get("startedAt") or d.get("createdAt"))
            ended   = _parse_vapi_iso(d.get("endedAt") or d.get("createdAt"))
            dur = None
            sa = _parse_vapi_iso(d.get("startedAt"))
            ea = _parse_vapi_iso(d.get("endedAt"))
            if sa and ea:
                dur = (ea - sa).total_seconds()

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE vapi_calls SET
                        started_at   = COALESCE(started_at, $1),
                        ended_at     = COALESCE(ended_at,   $2),
                        duration_sec = COALESCE(duration_sec, $3),
                        ended_reason = COALESCE(ended_reason, $4)
                    WHERE id = $5
                    """,
                    started, ended, dur, d.get("endedReason"), r["id"],
                )
            updated += 1

        for r in analysis_pending:
            outcome = await _apply_analysis_for_row(
                r["id"], r["vapi_call_id"], key, client,
            )
            if outcome["analysis_written"]:
                analysis_filled += 1
            inventory_rows += outcome["inventory_rows"]

    return {
        "checked":         len(inflight),
        "updated":         updated,
        "still_pending":   still_pending,
        "analysis_checked": len(analysis_pending),
        "analysis_filled": analysis_filled,
        "inventory_rows":  inventory_rows,
    }


# ── Durable analysis poller ─────────────────────────────────────────────────
#
# Long-running background task started by main.lifespan. Replaces the fragile
# per-call asyncio.create_task() 90s backstop that fired-and-forgot — those
# tasks die silently on process restart (Railway redeploy, scaling event, etc.)
# and the row would stay forever without summary or inventory mirror.
#
# This poller restarts cleanly with the process: on every boot the loop
# spins up and immediately picks up any analysis-pending rows in the DB.

_ANALYSIS_POLLER_INTERVAL_SEC = 60


async def _poll_analysis_pending_once() -> dict:
    """One iteration: pick up to 100 candidate rows (same predicate as the
    analysis pass of /reconcile_inflight), try to apply VAPI analysis to
    each. No-op when VAPI_PRIVATE_KEY is missing."""
    key = os.getenv("VAPI_PRIVATE_KEY")
    if not key:
        return {"skipped": "no_vapi_key"}
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, vapi_call_id
            FROM vapi_calls
            WHERE vapi_call_id IS NOT NULL
              AND ended_reason IS NOT NULL
              AND COALESCE(is_voicemail, false) = false
              AND duration_sec IS NOT NULL
              AND duration_sec >= 5
              AND (summary IS NULL OR per_ticket_results IS NULL)
              AND inventory_mirrored_at IS NULL
              AND received_at < NOW() - INTERVAL '90 seconds'
              AND received_at > NOW() - INTERVAL '2 hours'
            ORDER BY received_at DESC
            LIMIT 100
            """
        )
    if not rows:
        return {"checked": 0, "filled": 0, "inventory_rows": 0}

    filled = 0
    inv_rows = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for r in rows:
            outcome = await _apply_analysis_for_row(
                r["id"], r["vapi_call_id"], key, client,
            )
            if outcome["analysis_written"]:
                filled += 1
            inv_rows += outcome["inventory_rows"]
    if filled or inv_rows:
        logger.info(
            "vapi analysis poller: filled=%d inventory_rows=%d (of %d candidates)",
            filled, inv_rows, len(rows),
        )
    return {"checked": len(rows), "filled": filled, "inventory_rows": inv_rows}


async def analysis_poller_loop():
    """Run _poll_analysis_pending_once() forever on a fixed interval.
    Exceptions in any one iteration get logged and swallowed — never let
    a transient VAPI error kill the loop. Intended to be launched as
    asyncio.create_task() from FastAPI's lifespan startup."""
    logger.info("vapi analysis poller: starting (interval=%ds)", _ANALYSIS_POLLER_INTERVAL_SEC)
    # Small initial delay so we don't compete with the heavier startup work
    # the rest of the app does in the first 30s after boot.
    try:
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        return
    while True:
        try:
            await _poll_analysis_pending_once()
        except asyncio.CancelledError:
            logger.info("vapi analysis poller: cancelled")
            return
        except Exception:
            logger.exception("vapi analysis poller: iteration failed")
        try:
            await asyncio.sleep(_ANALYSIS_POLLER_INTERVAL_SEC)
        except asyncio.CancelledError:
            return
