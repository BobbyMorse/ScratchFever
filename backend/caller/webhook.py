"""
Twilio webhook endpoints and ConversationRelay WebSocket handler.

TwiML flow:
  1. Twilio outbound call connects → POST /caller/twiml/{queue_id}
     → Returns <Connect><ConversationRelay> TwiML pointing to our WS
  2. Twilio opens WS to /caller/ws/{queue_id}
     → Claude drives the conversation in real-time
  3. Twilio calls POST /caller/status/{queue_id} on call completion
     → We finalize the result in the DB
"""
from __future__ import annotations
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Form, Request
from fastapi.responses import Response

from backend.caller.db import (
    get_campaign, get_queue_stats, mark_calling, save_result,
    update_queue_status,
)
from backend.caller.dialogue import (
    extract_result, format_transcript, opening_line,
    should_end_call, stream_response,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# queue_id → {campaign, queue_item} stored here when call is initiated
_pending_calls: dict[int, dict] = {}


def register_call(queue_id: int, campaign: dict, queue_item: dict):
    _pending_calls[queue_id] = {"campaign": campaign, "queue_item": queue_item}


def _base_url() -> str:
    return os.getenv("CALLER_BASE_URL", "http://localhost:8000").rstrip("/")


# ── TwiML endpoint ────────────────────────────────────────────────────────────

@router.post("/caller/twiml/{queue_id}", include_in_schema=False)
async def twiml_handler(queue_id: int):
    ctx = _pending_calls.get(queue_id)
    if not ctx:
        # Fallback if context was lost (e.g. server restart)
        return Response(
            content="<Response><Hangup/></Response>",
            media_type="application/xml",
        )

    campaign = ctx["campaign"]
    ws_url = f"{_base_url().replace('http', 'ws')}/caller/ws/{queue_id}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <ConversationRelay url="{ws_url}" dtmfDetection="false" interruptible="true">
      <Parameter name="game_name" value="{campaign['game_name']}"/>
      <Parameter name="game_number" value="{campaign.get('game_number', '')}"/>
      <Parameter name="game_price" value="{campaign.get('game_price', 0)}"/>
      <Parameter name="queue_id" value="{queue_id}"/>
      <Parameter name="campaign_id" value="{campaign['id']}"/>
    </ConversationRelay>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


# ── ConversationRelay WebSocket handler ───────────────────────────────────────

@router.websocket("/caller/ws/{queue_id}")
async def conversation_ws(websocket: WebSocket, queue_id: int):
    await websocket.accept()

    messages: list[dict] = []
    game_info: dict = {}
    campaign_id: int | None = None
    call_sid: str | None = None
    turn_count = 0

    try:
        while True:
            data = await websocket.receive_json()
            event = data.get("type") or data.get("event")

            # ── Setup: Twilio just connected ──────────────────────────────────
            if event == "setup":
                call_sid = data.get("callSid")
                params = data.get("customParameters", {})
                game_info = {
                    "game_name": params.get("game_name", ""),
                    "game_number": params.get("game_number", ""),
                    "game_price": float(params.get("game_price", 0)),
                }
                campaign_id = int(params.get("campaign_id", 0)) or None
                logger.info("ConversationRelay setup: call=%s game=%s", call_sid, game_info["game_name"])

                # Claude's opening line
                greeting = opening_line(game_info)
                await websocket.send_json({"type": "text", "token": greeting, "last": True})
                messages.append({"role": "assistant", "content": greeting})

            # ── Prompt: clerk said something ──────────────────────────────────
            elif event == "prompt":
                speech = data.get("voicePrompt", "").strip()
                if not speech:
                    continue
                turn_count += 1
                messages.append({"role": "user", "content": speech})
                logger.info("[call %s] Store: %s", call_sid, speech)

                # Stream Claude's response back token-by-token
                full_response = ""
                first_token = True
                async for token in stream_response(messages, game_info):
                    if token.startswith("\x00"):
                        # Sentinel: full accumulated text
                        full_response = token[1:]
                        break
                    if first_token:
                        first_token = False
                    await websocket.send_json({"type": "text", "token": token, "last": False})

                await websocket.send_json({"type": "text", "token": "", "last": True})
                messages.append({"role": "assistant", "content": full_response})
                logger.info("[call %s] Caller: %s", call_sid, full_response)

                # End call if Claude said goodbye or we've gone long enough
                if should_end_call(full_response) or turn_count >= 6:
                    await websocket.send_json({"type": "end"})
                    break

            # ── Call ended (normal or remote hangup) ─────────────────────────
            elif event == "end":
                logger.info("[call %s] Call ended", call_sid)
                break

            # ── Interrupt: store spoke mid-TTS ───────────────────────────────
            elif event == "interrupt":
                pass  # Twilio already stopped playback; next prompt will come through

    except WebSocketDisconnect:
        logger.info("[call %s] WebSocket disconnected", call_sid)
    except Exception as exc:
        logger.error("[call %s] WS error: %s", call_sid, exc, exc_info=True)
    finally:
        # Post-call extraction + save
        if messages and call_sid:
            transcript = format_transcript(messages)
            game_name = game_info.get("game_name", "")
            try:
                result = await extract_result(transcript, game_name)
            except Exception as exc:
                logger.error("[call %s] extract_result failed: %s", call_sid, exc)
                result = {"has_game": None, "confidence": 0.0, "can_order": None, "notes": str(exc)}

            ctx = _pending_calls.pop(queue_id, {})
            q = ctx.get("queue_item", {})
            cid = campaign_id or q.get("campaign_id")

            if q.get("id") and cid:
                await save_result(
                    queue_id=q["id"],
                    campaign_id=cid,
                    call_sid=call_sid,
                    outcome="completed",
                    has_game=result.get("has_game"),
                    confidence=result.get("confidence", 0.0),
                    can_order=result.get("can_order"),
                    notes=result.get("notes", ""),
                    transcript=transcript,
                )
                await update_queue_status(q["id"], "done")
                logger.info("[call %s] Result saved: has_game=%s confidence=%.2f",
                            call_sid, result.get("has_game"), result.get("confidence", 0))


# ── Twilio status callback ────────────────────────────────────────────────────

@router.post("/caller/status/{queue_id}", include_in_schema=False)
async def status_callback(queue_id: int, request: Request):
    form = await request.form()
    call_status = form.get("CallStatus", "")
    call_sid = form.get("CallSid", "")
    answered_by = form.get("AnsweredBy", "")
    logger.info("Status callback queue=%d sid=%s status=%s answered_by=%s",
                queue_id, call_sid, call_status, answered_by)

    ctx = _pending_calls.get(queue_id)
    q = ctx.get("queue_item", {}) if ctx else {}

    terminal_no_contact = call_status in ("no-answer", "busy", "failed")

    if answered_by in ("machine_start", "machine_end_beep", "machine_end_silence"):
        # Voicemail detected — hang up and mark for retry
        _pending_calls.pop(queue_id, None)
        if q.get("id"):
            new_status = "pending" if (q.get("attempts", 0) or 0) < 3 else "voicemail"
            await update_queue_status(q["id"], new_status)

    elif terminal_no_contact:
        _pending_calls.pop(queue_id, None)
        if q.get("id"):
            new_status = "pending" if (q.get("attempts", 0) or 0) < 3 else "no_answer"
            await update_queue_status(q["id"], new_status)

    return Response(content="", status_code=204)


# ── Bland AI webhook ──────────────────────────────────────────────────────────

@router.post("/caller/bland/webhook", include_in_schema=False)
async def bland_webhook(request: Request):
    """
    Bland POSTs here when a call finishes.
    Payload includes the full transcript + metadata we attached at call time.
    """
    data = await request.json()

    call_id     = data.get("call_id", "")
    transcript  = data.get("transcript", "")
    answered_by = data.get("answered_by", "")
    call_length = data.get("call_length", 0)
    status      = data.get("status", "")
    metadata    = data.get("metadata") or {}

    queue_id    = metadata.get("queue_id")
    campaign_id = metadata.get("campaign_id")

    logger.info("Bland webhook call_id=%s queue_id=%s status=%s answered_by=%s length=%.1fs",
                call_id, queue_id, status, answered_by, call_length or 0)

    if not queue_id or not campaign_id:
        logger.warning("Bland webhook missing metadata: %s", metadata)
        return Response(content="ok")

    # Voicemail / no-answer — retry or mark
    if answered_by in ("voicemail", "machine") or not transcript:
        ctx = _pending_calls.pop(queue_id, {})
        q = ctx.get("queue_item", {})
        attempts = q.get("attempts", 1) or 1
        new_status = "pending" if attempts < 3 else "voicemail"
        await update_queue_status(queue_id, new_status)
        return Response(content="ok")

    # Extract structured result from transcript using Claude
    from backend.caller.db import get_campaign as _get_campaign
    campaign = await _get_campaign(campaign_id)
    game_name = campaign["game_name"] if campaign else ""

    try:
        result = await extract_result(transcript, game_name)
    except Exception as exc:
        logger.error("Bland extract_result failed call_id=%s: %s", call_id, exc)
        result = {"has_game": None, "confidence": 0.0, "can_order": None, "notes": str(exc)}

    await save_result(
        queue_id=queue_id,
        campaign_id=campaign_id,
        call_sid=call_id,
        outcome="completed",
        has_game=result.get("has_game"),
        confidence=result.get("confidence", 0.0),
        can_order=result.get("can_order"),
        notes=result.get("notes", ""),
        transcript=transcript,
        duration_sec=int(call_length or 0),
    )
    await update_queue_status(queue_id, "done")
    _pending_calls.pop(queue_id, None)

    logger.info("Bland result saved: queue=%d has_game=%s confidence=%.2f",
                queue_id, result.get("has_game"), result.get("confidence", 0))
    return Response(content="ok")
