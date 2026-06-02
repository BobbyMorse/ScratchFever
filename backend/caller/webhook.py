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
from backend.caller.api import get_runner

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
        ctx = _pending_calls.pop(queue_id, {})
        if call_sid:
            runner = get_runner()
            if runner:
                runner.call_completed(call_sid)
        # Post-call extraction + save
        if messages and call_sid:
            transcript = format_transcript(messages)
            game_name = game_info.get("game_name", "")
            try:
                result = await extract_result(transcript, game_name)
            except Exception as exc:
                logger.error("[call %s] extract_result failed: %s", call_sid, exc)
                result = {"has_game": None, "confidence": 0.0, "can_order": None, "notes": str(exc)}

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

    runner = get_runner()

    if answered_by in ("machine_start", "machine_end_beep", "machine_end_silence"):
        # Voicemail detected — hang up and mark for retry
        _pending_calls.pop(queue_id, None)
        if runner and call_sid:
            runner.call_completed(call_sid)
        if q.get("id"):
            new_status = "pending" if (q.get("attempts", 0) or 0) < 3 else "voicemail"
            await update_queue_status(q["id"], new_status)

    elif terminal_no_contact:
        _pending_calls.pop(queue_id, None)
        if runner and call_sid:
            runner.call_completed(call_sid)
        if q.get("id"):
            new_status = "pending" if (q.get("attempts", 0) or 0) < 3 else "no_answer"
            await update_queue_status(q["id"], new_status)

    return Response(content="", status_code=204)


# ── Twilio IVR (press 1 = has it, press 2 = doesn't) ─────────────────────────

@router.post("/caller/ivr/twiml/{queue_id}", include_in_schema=False)
async def ivr_twiml(queue_id: int):
    ctx = _pending_calls.get(queue_id)
    if not ctx and queue_id != 0:
        return Response(content="<Response><Hangup/></Response>", media_type="application/xml")

    campaign    = ctx["campaign"] if ctx else {}
    game_name   = campaign.get("game_name", "a scratch ticket")
    game_number = campaign.get("game_number", "")
    game_price  = float(campaign.get("game_price", 0))

    price_str = f"${game_price:.0f} " if game_price else ""
    num_str   = f" It's game number {game_number}." if game_number else ""

    message = (
        f"Hi, this is an automated lottery inventory check. "
        f"Do you currently have the {game_name} Massachusetts scratch ticket in stock? "
        f"It's the {price_str}ticket.{num_str} "
        f"Press 1 if you have it, or press 2 if you don't."
    )

    gather_url = f"{_base_url()}/caller/ivr/gather/{queue_id}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather numDigits="1" action="{gather_url}" method="POST" timeout="10">
    <Say voice="Polly.Joanna">{message}</Say>
  </Gather>
  <Say voice="Polly.Joanna">We didn't receive a response. Thank you, goodbye!</Say>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/caller/ivr/gather/{queue_id}", include_in_schema=False)
async def ivr_gather(queue_id: int, request: Request):
    form     = await request.form()
    digit    = form.get("Digits", "")
    call_sid = form.get("CallSid", "")

    has_game = True if digit == "1" else (False if digit == "2" else None)

    if queue_id != 0:
        ctx      = _pending_calls.pop(queue_id, {})
        q        = ctx.get("queue_item", {})
        campaign = ctx.get("campaign", {})
        if q.get("id") and campaign.get("id"):
            await save_result(
                queue_id=q["id"],
                campaign_id=campaign["id"],
                call_sid=call_sid or f"ivr_{queue_id}",
                outcome="completed",
                has_game=has_game,
                confidence=1.0 if digit in ("1", "2") else 0.0,
                can_order=None,
                notes=f"IVR keypress: {digit!r}",
                transcript=f"IVR: pressed {digit!r}",
            )
            await update_queue_status(q["id"], "done")
            logger.info("IVR result saved: queue=%d digit=%s has_game=%s", queue_id, digit, has_game)

    if has_game is True:
        reply = "Great, thank you so much! Have a great day!"
    elif has_game is False:
        reply = "No problem, thanks for letting us know! Have a great day!"
    else:
        reply = "Sorry, we didn't catch that. Thanks anyway, goodbye!"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">{reply}</Say>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/caller/ivr/status/{queue_id}", include_in_schema=False)
async def ivr_status(queue_id: int, request: Request):
    form        = await request.form()
    call_status = form.get("CallStatus", "")
    call_sid    = form.get("CallSid", "")
    answered_by = form.get("AnsweredBy", "")
    logger.info("IVR status callback queue=%d sid=%s status=%s answered_by=%s",
                queue_id, call_sid, call_status, answered_by)

    if queue_id == 0:
        return Response(content="", status_code=204)

    ctx = _pending_calls.get(queue_id)
    q   = ctx.get("queue_item", {}) if ctx else {}

    runner = get_runner()

    if answered_by in ("machine_start", "machine_end_beep", "machine_end_silence"):
        _pending_calls.pop(queue_id, None)
        if runner and call_sid:
            runner.call_completed(call_sid)
        if q.get("id"):
            new_status = "pending" if (q.get("attempts", 0) or 0) < 3 else "voicemail"
            await update_queue_status(q["id"], new_status)
    elif call_status in ("no-answer", "busy", "failed"):
        _pending_calls.pop(queue_id, None)
        if runner and call_sid:
            runner.call_completed(call_sid)
        if q.get("id"):
            new_status = "pending" if (q.get("attempts", 0) or 0) < 3 else "no_answer"
            await update_queue_status(q["id"], new_status)

    return Response(content="", status_code=204)
