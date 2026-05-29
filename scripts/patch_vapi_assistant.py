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


STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {
        "has_game": {
            "type": "boolean",
            "description": (
                "True if the store CURRENTLY HAS IN STOCK at least one of the "
                "tickets listed in ticketsToCheck. False if they have none. "
                "Set null only if the human refused to check or the answer was "
                "genuinely ambiguous."
            ),
        },
        "confidence": {
            "type": "number",
            "description": (
                "0 to 1, how confident you are in the has_game answer based on "
                "the human's responses. Use 0.9+ when the human explicitly "
                "confirmed/denied, 0.5-0.8 when implied, below 0.5 when unsure."
            ),
        },
        "notes": {
            "type": "string",
            "description": (
                "Any specifics the store mentioned — which tickets they have, "
                "low stock, when they expect restock, etc. Keep under 200 chars."
            ),
        },
    },
    "required": ["has_game", "confidence"],
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
