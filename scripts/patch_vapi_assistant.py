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

SYSTEM_PROMPT = """You are an automated inventory assistant calling retail stores to check scratch-off lottery ticket availability. Sound operational and legitimate, not salesy.

## Opening behavior
- Move through the "automated assistant" disclosure FAST — one short clause, then immediately into the real question. Don't linger on the intro.
- Use the provided firstMessage as the default opener.
- If they pick up mid-sentence or sound rushed, skip ahead to the ticket question without re-greeting.

## If the retailer is confused (e.g., "what?", "say that again?", "who is this?")
- Do NOT restart the full disclosure.
- Use a compressed recovery line once, then move on:
  "Sure — automated inventory check for scratch tickets. Just a couple quick questions."
- Then repeat only the immediate question you need next.

## Conversational style
- Keep turns short (1 sentence where possible).
- Ask one question at a time.
- If they're busy, offer a quick exit: ask if there's a better time, or if someone else handles lottery tickets.

## Goal
- Confirm whether they sell scratch-off tickets, then proceed with your inventory questions efficiently.

## After they confirm they sell scratch tickets
Ask about these specific tickets, one at a time, by exact name:

{{ticketsToCheck}}

For each one, find out whether the store currently has it in stock.
Use the EXACT name shown above when reporting in per_ticket_results
(do not include the price in the name).

If they say no to all of them, that's fine — still report each ticket
with has_game: false.

## Compliance
- If asked, be transparent: you are an automated system calling to collect inventory availability information for scratch tickets.
- Never claim to be a person.
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
                            "Anything specific the store said about THIS "
                            "ticket — low stock, sold out yesterday, etc."
                        ),
                    },
                },
                "required": ["name", "has_game"],
            },
        },
        "summary_notes": {
            "type": "string",
            "description": "Overall observations about the call (max 240 chars).",
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
        "serverMessages": ["end-of-call-report"],
        "firstMessage": (
            "Hi — automated inventory check for scratch tickets. "
            "Do you currently sell them?"
        ),
        "model": {
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
            ],
        },
        "analysisPlan": {
            "structuredDataPlan": {
                "enabled": True,
                "schema": STRUCTURED_SCHEMA,
            },
            "summaryPlan": {"enabled": True},
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
