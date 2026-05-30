"""
RevenueCat webhook — mirrors the `pro` entitlement state into users.pro_until.

RevenueCat is the source of truth for subscription state. This endpoint is the
only place where users.pro_until is written. Clients should never tell the server
they are Pro; the server learns it from RC.

Auth model: RC sends a shared secret in the Authorization header (configured in
the RC dashboard → Project Settings → Webhooks → Authorization header value).
Compared in constant time against REVENUECAT_WEBHOOK_SECRET env var.
"""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from backend.users import set_user_pro_until

router = APIRouter()
logger = logging.getLogger(__name__)

PRO_ENTITLEMENT_ID = "pro"
# For lifetime / non-renewing purchases RC sends expiration_at_ms = null.
# We mirror that as a far-future timestamp so the simple `pro_until > NOW()` check works.
LIFETIME_SENTINEL = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)


def _verify_auth(authorization: Optional[str]) -> None:
    expected = os.getenv("REVENUECAT_WEBHOOK_SECRET", "")
    if not expected:
        logger.error("REVENUECAT_WEBHOOK_SECRET not set — refusing all RC webhooks")
        raise HTTPException(status_code=503, detail="Webhook not configured")
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook auth")


def _parse_user_id(app_user_id: Optional[str]) -> Optional[int]:
    """RC anonymous IDs look like `$RCAnonymousID:abc123` and have no backing user."""
    if not app_user_id or app_user_id.startswith("$RCAnonymousID"):
        return None
    try:
        return int(app_user_id)
    except ValueError:
        return None


def _expiration_from_event(event: dict) -> Optional[dt.datetime]:
    ms = event.get("expiration_at_ms")
    if ms is None:
        return LIFETIME_SENTINEL
    try:
        return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


@router.post("/api/revenuecat/webhook")
async def revenuecat_webhook(request: Request, authorization: Optional[str] = Header(None)):
    _verify_auth(authorization)

    payload = await request.json()
    event = payload.get("event") or {}
    event_type = event.get("type", "")
    entitlements = event.get("entitlement_ids") or []

    # Only react to events that touch the "pro" entitlement.
    # PRODUCT_CHANGE / TRANSFER may have empty entitlement_ids — we still handle them.
    touches_pro = (
        PRO_ENTITLEMENT_ID in entitlements
        or event_type in ("TRANSFER", "EXPIRATION", "CANCELLATION", "UNCANCELLATION", "TEST")
    )
    if not touches_pro:
        return {"ok": True, "ignored": event_type}

    grant_events = {"INITIAL_PURCHASE", "RENEWAL", "NON_RENEWING_PURCHASE",
                    "PRODUCT_CHANGE", "UNCANCELLATION"}
    revoke_events = {"EXPIRATION"}
    # CANCELLATION + BILLING_ISSUE = user still has access until period end; ignore.

    if event_type in grant_events:
        uid = _parse_user_id(event.get("app_user_id"))
        if uid is None:
            return {"ok": True, "skipped": "anonymous or invalid app_user_id"}
        expires = _expiration_from_event(event)
        if expires is None:
            return {"ok": True, "skipped": "bad expiration"}
        await set_user_pro_until(uid, expires)
        logger.info("RC grant: user %d pro until %s (%s)", uid, expires.isoformat(), event_type)

    elif event_type in revoke_events:
        uid = _parse_user_id(event.get("app_user_id"))
        if uid is not None:
            await set_user_pro_until(uid, None)
            logger.info("RC revoke: user %d (%s)", uid, event_type)

    elif event_type == "TRANSFER":
        # transferred_to inherits the entitlement; transferred_from loses it.
        expires = _expiration_from_event(event) or LIFETIME_SENTINEL
        for raw in event.get("transferred_to") or []:
            uid = _parse_user_id(raw)
            if uid is not None:
                await set_user_pro_until(uid, expires)
                logger.info("RC transfer-to: user %d", uid)
        for raw in event.get("transferred_from") or []:
            uid = _parse_user_id(raw)
            if uid is not None:
                await set_user_pro_until(uid, None)
                logger.info("RC transfer-from: user %d", uid)

    elif event_type == "TEST":
        logger.info("RC test webhook received")

    return {"ok": True}
