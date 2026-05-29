"""
Inspect a VAPI assistant's webhook + analysis config.

Usage:
    # PowerShell
    $env:VAPI_PRIVATE_KEY = "<your-key>"
    python scripts/inspect_vapi_assistant.py

    # bash
    export VAPI_PRIVATE_KEY="<your-key>"
    python scripts/inspect_vapi_assistant.py

Reads the assistant id from the first CLI arg, falls back to a baked-in
default for ScratchFever.
"""
from __future__ import annotations
import json
import os
import sys
import httpx


ASSISTANT_ID = sys.argv[1] if len(sys.argv) > 1 else "e87d7468-01f9-4a83-8a62-2feef8a4ab44"


def main() -> int:
    key = os.environ.get("VAPI_PRIVATE_KEY")
    if not key:
        print("ERROR: VAPI_PRIVATE_KEY not set in this shell.")
        print("  PowerShell:  $env:VAPI_PRIVATE_KEY = \"your-key\"")
        print("  bash:        export VAPI_PRIVATE_KEY=\"your-key\"")
        return 1

    with httpx.Client(timeout=20.0) as c:
        r = c.get(
            f"https://api.vapi.ai/assistant/{ASSISTANT_ID}",
            headers={"Authorization": f"Bearer {key}"},
        )
    print(f"GET /assistant/{ASSISTANT_ID} → {r.status_code}")
    if r.status_code != 200:
        print(r.text[:1000])
        return 2

    a = r.json()

    print("\n══ Webhook configuration ══")
    print(f"server.url:        {(a.get('server') or {}).get('url')}")
    print(f"server.secret set: {bool((a.get('server') or {}).get('secret'))}")
    print(f"serverUrl (legacy):       {a.get('serverUrl')}")
    print(f"serverUrlSecret (legacy): {'SET' if a.get('serverUrlSecret') else 'NOT SET'}")
    print(f"serverMessages:    {a.get('serverMessages')}")
    print(f"clientMessages:    {a.get('clientMessages')}")

    print("\n══ Analysis plan (structured outputs) ══")
    plan = a.get("analysisPlan") or {}
    sd = plan.get("structuredDataPlan") or {}
    print("structuredDataPlan.enabled:", sd.get("enabled"))
    if sd.get("schema"):
        print("structuredDataPlan.schema:")
        print(json.dumps(sd["schema"], indent=2)[:2000])
    if sd.get("messages"):
        print("structuredDataPlan.messages[0]:")
        print(json.dumps(sd["messages"][0], indent=2)[:1500])
    smry = plan.get("summaryPlan") or {}
    print("summaryPlan.enabled:", smry.get("enabled"))
    succ = plan.get("successEvaluationPlan") or {}
    print("successEvaluationPlan.enabled:", succ.get("enabled"))

    print("\n══ Model + prompt ══")
    m = a.get("model") or {}
    print(f"provider/model: {m.get('provider')} / {m.get('model')}")
    msgs = m.get("messages") or []
    for i, msg in enumerate(msgs[:3]):
        role = msg.get("role")
        content = (msg.get("content") or "")
        print(f"\n— message[{i}] role={role} (len {len(content)}) —")
        print(content[:1500])
        if len(content) > 1500:
            print("…(truncated)")
    if len(msgs) > 3:
        print(f"\n(+ {len(msgs) - 3} more messages)")

    print("\n══ Voice / transport ══")
    print(f"voice.provider:    {(a.get('voice') or {}).get('provider')}")
    print(f"voice.voiceId:     {(a.get('voice') or {}).get('voiceId')}")
    print(f"firstMessage:      {a.get('firstMessage')!r}")
    print(f"endCallPhrases:    {a.get('endCallPhrases')}")
    print(f"maxDurationSeconds:{a.get('maxDurationSeconds')}")

    print("\n══ Variables in prompts (templated) ══")
    used_vars = set()
    import re
    for msg in msgs:
        for v in re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", msg.get("content") or ""):
            used_vars.add(v)
    fm = a.get("firstMessage") or ""
    for v in re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", fm):
        used_vars.add(v)
    print(sorted(used_vars))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
