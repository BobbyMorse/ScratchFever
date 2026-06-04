"""
Patch a VAPI assistant to:
  1. Subscribe to the `end-of-call-report` server message
  2. Enable structured-data extraction with the schema our webhook parses
  3. Enable a summary
  4. Inject {{ticketsToCheck}} into the system prompt so the assistant
     actually knows which tickets ScratchFever picked for this call

Usage:
    export VAPI_PRIVATE_KEY="..."   # or $env:VAPI_PRIVATE_KEY in PowerShell
    python scripts/patch_vapi_assistant.py

Pass an assistant id as the first arg to override the baked-in default.
"""
from __future__ import annotations
import json
import os
import sys
import httpx


ASSISTANT_ID = sys.argv[1] if len(sys.argv) > 1 else "e87d7468-01f9-4a83-8a62-2feef8a4ab44"


WEBHOOK_URL = "https://scratchfever.app/api/vapi/webhook"

SYSTEM_PROMPT = """You are calling retail stores to check scratch-off lottery ticket availability. Sound like a normal person making a quick business call — calm, casual, brief.

## Opening behavior
- Do NOT volunteer that you are automated, AI, a bot, or an assistant. Never lead with that.
- Use the provided firstMessage as the default opener. It's just a short question — no intro, no company name unless asked.
- If they pick up mid-sentence or sound rushed, skip straight to the ticket question.

## If they're confused by audio ("what?", "say that again?", "speak up")
- Just repeat the immediate question more clearly. No identity talk.
  "Sorry — do you sell scratch-off tickets there?"

## If they directly ask about your identity ("who is this?", "is this a robot?", "am I talking to a person?", "is this AI?")
- You MUST acknowledge honestly. Never deny being automated. Never claim to be a person.
- One short word of confirmation, then immediately continue with the ticket question:
  "Yeah, automated — looking for {{ticketsToCheck}}, do you have any?"
  "It's an AI, yep — quick question, got any of these in stock?"

## If they ask who's calling / what company
- Say "ScratchFever — we track scratch-ticket inventory." Then continue.

## Conversational style
- Keep turns SHORT (one sentence where possible). No filler ("great", "perfect", "awesome").
- One question at a time.
- If they're busy: ask if there's a better time, or if someone else handles lottery tickets.

## Goal
- Confirm they sell scratch tickets, then check each ticket below efficiently.

## After they confirm they sell scratch tickets
Ask about these specific tickets, one at a time, by exact name:

{{ticketsToCheck}}

For each one, find out whether the store currently has it in stock.
Use the EXACT name shown above when reporting in per_ticket_results
(do not include the price in the name).

If they say no to all of them, that's fine — still report each ticket
with has_game: false.

## Closing the call
- Once you have answers for every ticket (or they've confirmed they don't sell scratch tickets at all), close with a short thanks and end:
  "Got it — thanks, have a good one."
  "Appreciate it, thanks."
- Don't drag the wrap-up. One short thank-you and you're done.

## Honesty
- If directly asked whether you're a person, a bot, AI, automated, or a recording — answer truthfully. You are automated.
- Do not lie about being human. But also do not announce it unprompted.
"""


STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {
        "per_ticket_results": {
            "type": "array",
            "description": (
                "ONE entry per ticket listed in ticketsToCheck. Always include "
                "every ticket the assistant asked about, even if the store said "
                "no to all of them."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "EXACT game name as it appeared in ticketsToCheck "
                            "(do not include the price). Example: '300X' or "
                            "'Fabulous Fortune'."
                        ),
                    },
                    "price": {
                        "type": "number",
                        "description": "Ticket price in dollars (10, 30, etc).",
                    },
                    "has_game": {
                        "type": "boolean",
                        "description": (
                            "True if the store currently has THIS specific "
                            "ticket in stock. False if they don't. Set null "
                            "only when truly unable to tell."
                        ),
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0 to 1 — how confident in this answer.",
                    },
                    "notes": {
                        "type": "string",
                        "description": (
                            "Brief PARAPHRASE of what the store said about THIS "
                            "ticket (e.g., 'recently sold out', 'low stock', "
                            "'never carried it'). DO NOT include verbatim "
                            "quotes from what the customer said — paraphrase "
                            "only. Never use quotation marks. Max ~80 chars."
                        ),
                    },
                },
                "required": ["name", "has_game"],
            },
        },
        "summary_notes": {
            "type": "string",
            "description": (
                "Brief PARAPHRASE of overall observations about the call "
                "(max 240 chars). DO NOT include verbatim quotes from what "
                "the customer said — paraphrase only. Never use quotation marks."
            ),
        },
        "answered_phone": {
            "type": "boolean",
            "description": (
                "True if a real voice (human OR clearly-human IVR menu) answered "
                "the line and spoke to the bot. False if it was a voicemail "
                "greeting, the customer never picked up, the line was busy, or "
                "the call ended at transport/error before anyone spoke."
            ),
        },
        "confirmed_sells_scratch": {
            "type": "boolean",
            "description": (
                "True if the person on the line explicitly confirmed the store "
                "sells scratch-off tickets ('yeah we have them', 'yes', etc.). "
                "False if they said no, the store doesn't carry them, or "
                "redirected elsewhere. Null if the call ended before they "
                "answered that question."
            ),
        },
        "inventory_actually_checked": {
            "type": "boolean",
            "description": (
                "True if the person clearly took time to LOOK at inventory "
                "(audible pause, moving away from phone, 'let me check', "
                "checking with coworker, etc.). False if they answered "
                "instantly off the top of their head without checking. Null "
                "if not enough signal to tell."
            ),
        },
        "tickets_asked_count": {
            "type": "integer",
            "description": (
                "How many DISTINCT tickets from ticketsToCheck the bot actually "
                "got to ask about during the call (0 if the call ended before "
                "any ticket-specific question)."
            ),
        },
        "tickets_answered_count": {
            "type": "integer",
            "description": (
                "How many tickets the customer gave a definite yes/no answer "
                "on. Always ≤ tickets_asked_count."
            ),
        },
        "customer_disposition": {
            "type": "string",
            "enum": [
                "cooperative",
                "rushed",
                "frustrated",
                "confused",
                "rude",
                "uninterested",
                "no_speech",
                "unknown",
            ],
            "description": (
                "Best read of the customer's tone/mood across the call. "
                "'cooperative' = engaged and answered. 'rushed' = answered but "
                "wanted to get off fast. 'frustrated' = annoyed, raised voice, "
                "or curt with the bot. 'confused' = didn't understand what was "
                "being asked. 'rude' = hostile or insulting. 'uninterested' = "
                "dismissive, declined to engage. 'no_speech' = call ended before "
                "the customer said anything meaningful. 'unknown' only if truly "
                "unreadable."
            ),
        },
        "ended_early_reason": {
            "type": "string",
            "description": (
                "If the customer ended the call BEFORE all tickets were answered, "
                "a short PARAPHRASE on why (max 80 chars). Examples: busy with "
                "customers, didn't want to talk to AI, wrong department, "
                "frustrated by question. DO NOT quote the customer verbatim. "
                "Empty string if all tickets were covered."
            ),
        },
    },
    "required": ["per_ticket_results"],
}


def main() -> int:
    key = os.environ.get("VAPI_PRIVATE_KEY")
    if not key:
        print("ERROR: VAPI_PRIVATE_KEY not set in this shell.")
        return 1

    patch = {
        "server": {"url": WEBHOOK_URL},
        # status-update covers terminal failure modes that never emit an
        # end-of-call-report (transport errors, customer-did-not-answer, busy)
        # — without it those calls stay "In flight" forever locally.
        "serverMessages": ["end-of-call-report", "status-update"],
        "firstMessage": (
            "Hi — quick question, do you sell scratch-off tickets there?"
        ),
        "model": {
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
            ],
        },
        "voice": {
            "provider": "vapi",
            "voiceId": "Elliot",
        },
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "en",
        },
        "startSpeakingPlan": {
            "waitSeconds": 0.2,
            "smartEndpointingPlan": {"provider": "livekit"},
        },
        "responseDelaySeconds": 0,
        "numWordsToInterruptAssistant": 2,
        "backgroundDenoisingEnabled": True,
        "analysisPlan": {
            "structuredDataPlan": {
                "enabled": True,
                "schema": STRUCTURED_SCHEMA,
            },
            # Use VAPI's default summary prompt — overriding `messages` without
            # the right `{{transcript}}` placeholder makes VAPI emit no summary
            # at all. The default is good enough; disposition/funnel detail
            # lives in structuredData instead.
            "summaryPlan": {"enabled": True},
        },
        # Two-party-consent states (MA, CA, FL, IL, MD, MT, NV, NH, PA, WA, CT) —
        # never store the audio. Transcript still streams during the call so
        # the LLM and structured extraction work, but no .wav is persisted.
        "artifactPlan": {
            "recordingEnabled": False,
            "videoRecordingEnabled": False,
        },
    }

    print(f"PATCH /assistant/{ASSISTANT_ID}")
    print(json.dumps(patch, indent=2))
    print()

    with httpx.Client(timeout=20.0) as c:
        r = c.patch(
            f"https://api.vapi.ai/assistant/{ASSISTANT_ID}",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json=patch,
        )
    print(f"Response: {r.status_code}")
    if r.status_code >= 300:
        print(r.text[:2000])
        return 2

    a = r.json()
    print("After patch:")
    print(f"  serverMessages:                  {a.get('serverMessages')}")
    sd = ((a.get("analysisPlan") or {}).get("structuredDataPlan") or {})
    print(f"  analysisPlan.structuredDataPlan.enabled: {sd.get('enabled')}")
    sm = ((a.get("analysisPlan") or {}).get("summaryPlan") or {})
    print(f"  analysisPlan.summaryPlan.enabled:        {sm.get('enabled')}")
    print(f"  firstMessage:                    {a.get('firstMessage')!r}")
    msgs = (a.get("model") or {}).get("messages") or []
    sys_msg = next((m.get("content", "") for m in msgs if m.get("role") == "system"), "")
    has_var = "{{ticketsToCheck}}" in sys_msg
    print(f"  system prompt has ticketsToCheck:        {has_var}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
