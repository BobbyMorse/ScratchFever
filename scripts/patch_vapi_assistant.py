"""
Patch a VAPI assistant to:
  1. Subscribe to the `end-of-call-report` server message
  2. Enable structured-data extraction with the schema our webhook parses
  3. Enable a summary

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
        "serverMessages": ["end-of-call-report"],
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
