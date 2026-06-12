"""
Resend email wrapper. Lazy, fire-and-forget friendly, never raises.

Env vars:
  RESEND_API_KEY  required to actually send (no-ops without it, so local
                  dev doesn't need a key)
  RESEND_FROM     From address, e.g. "ScratchFrenzy <noreply@scratchfrenzy.app>"
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://api.resend.com/emails"
_DEFAULT_FROM = "ScratchFrenzy <noreply@scratchfrenzy.app>"


def app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "https://scratchfrenzy.app").strip().rstrip("/") or "https://scratchfrenzy.app"


async def send_email(
    to: str,
    subject: str,
    html: str,
    *,
    text: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> bool:
    """Send an email via Resend. Returns True on success, False otherwise.
    Never raises — failures are logged and swallowed so callers don't have
    to wrap every call in try/except."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.warning("RESEND_API_KEY not set — skipping email to %s (%r)", to, subject)
        return False
    if not to:
        return False

    payload = {
        "from": os.getenv("RESEND_FROM", "").strip() or _DEFAULT_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                _API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        if r.status_code >= 300:
            logger.warning("Resend send failed (%s) to %s: %s", r.status_code, to, r.text[:500])
            return False
        return True
    except Exception as exc:
        logger.warning("Resend send raised for %s: %s", to, exc, exc_info=True)
        return False
