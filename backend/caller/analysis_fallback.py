"""
Claude Haiku fallback for VAPI's structuredData extractor.

VAPI's own analysisPlan flakes sometimes — LLM timeouts, queue backups,
service degradation. When that happens the call still finishes and we
still get a transcript in the webhook payload, but `analysis.structuredData`
is null and we lose the inventory data unless we extract it ourselves.

This module runs Claude Haiku 4.5 on the transcript to produce the same
shape VAPI would have produced, so the rest of the pipeline (per_ticket
mirror into inventory_reports, dashboard Result badge, etc.) keeps working.

Compliance: the transcript stays in-memory only — we extract structured
fields and return those. Callers are responsible for not persisting the
raw transcript, matching the two-party-consent guarantee elsewhere in
this module.
"""
from __future__ import annotations
import json
import logging
import os
import re
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> Optional[anthropic.AsyncAnthropic]:
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            return None
        _client = anthropic.AsyncAnthropic(api_key=key)
    return _client


# Cacheable instructions block — same across every call, so cache_control
# turns repeat extractions into ~0-cost prompt-cache hits.
_EXTRACTION_INSTRUCTIONS = """\
You are extracting structured data from a brief outbound phone-call transcript.

The caller is an AI agent asking a US convenience store / lottery retailer
whether they have specific scratch-off lottery tickets in stock. Your job is to
return one JSON object summarizing what was learned.

Return JSON with this exact schema (no markdown, no prose):
{
  "summary": "<one sentence describing what the AI caller asked and what the retailer said>",
  "per_ticket_results": [
    {"name": "<exact ticket name from the asked list>",
     "has_game": true | false | null,
     "confidence": 0.0-1.0,
     "notes": "<optional short note, e.g. quantity remark>"}
  ],
  "answered_phone": true | false,
  "confirmed_sells_scratch": true | false | null,
  "inventory_actually_checked": true | false | null,
  "customer_disposition": "cooperative" | "rushed" | "frustrated" | "confused" | "rude" | "uninterested" | "no_speech" | "unknown",
  "ended_early_reason": "<null OR brief reason the call ended early>"
}

Rules:
- One entry in per_ticket_results per ticket the caller asked about, using the
  exact name from the asked list — even if the retailer never confirmed.
  Use has_game=null when unclear.
- has_game=true ONLY if the retailer explicitly confirmed they have it.
- has_game=false ONLY if the retailer explicitly said they don't.
- If the retailer hung up immediately without addressing any ticket, return
  per_ticket_results: [] and ended_early_reason briefly explaining.
- confidence reflects how sure you are about has_game (1.0 = explicit, 0.5 = inferred).
- Return ONLY the JSON object. No code fences, no commentary.
"""


async def extract_from_transcript(
    transcript: str,
    asked_tickets: list[str],
) -> Optional[dict]:
    """Run Claude Haiku on the transcript to produce VAPI-shaped structuredData.
    Returns None on any failure — caller treats that as no fallback available
    and falls through to whatever default behavior they had."""
    client = _get_client()
    if not client:
        return None
    if not transcript or not transcript.strip():
        return None
    if not asked_tickets:
        return None

    asked_block = "\n".join(f"- {t}" for t in asked_tickets)
    user_msg = (
        f"Tickets the caller asked about:\n{asked_block}\n\n"
        f"Transcript:\n{transcript[:12000]}"
    )

    try:
        msg = await client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=1024,
            system=[{
                "type": "text",
                "text": _EXTRACTION_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        logger.warning("Haiku fallback extraction failed: %s", exc)
        return None

    text = "".join(getattr(b, "text", "") for b in msg.content).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except Exception:
        logger.warning("Haiku fallback returned non-JSON: %r", text[:300])
        return None
    if not isinstance(data, dict):
        return None
    return data
