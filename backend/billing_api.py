"""
Stripe billing + beta codes.

Stripe is the web-side payment provider. RevenueCat handles mobile (App
Store / Play). Both write into the same `users.pro_until` column — that
column is the entitlement source of truth for the app.

Plans live in Stripe; this module just reads the two price IDs out of env
so amounts can be A/B'd without code changes.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

import stripe

from backend.database import get_pool
from backend.users import (
    get_user_by_id,
    get_user_by_stripe_customer,
    get_user_pro_until,
    require_admin,
    require_member,
    set_user_pro_until,
    set_user_stripe_ids,
)

router = APIRouter()
logger = logging.getLogger(__name__)

PLAN_MONTHLY = "monthly"
PLAN_YEARLY = "yearly"
BETA_CODE_DURATION_DAYS = 180


def _trial_days() -> int:
    # Launch offer knob — set STRIPE_TRIAL_DAYS=60 in prod during the intro
    # window, drop to 0 to end it. Mirrors the intro offer we configured on
    # the ASC / RC side so both platforms honor the same free window.
    raw = os.getenv("STRIPE_TRIAL_DAYS", "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _stripe_key() -> str:
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Billing not configured")
    stripe.api_key = key
    return key


def _price_id(plan: str) -> str:
    env_name = "STRIPE_PRICE_MONTHLY" if plan == PLAN_MONTHLY else "STRIPE_PRICE_YEARLY"
    pid = os.getenv(env_name, "").strip()
    if not pid:
        raise HTTPException(status_code=503, detail=f"{env_name} not set")
    return pid


def _base_url(request: Request) -> str:
    return os.getenv("APP_BASE_URL", "").strip() or str(request.base_url).rstrip("/")


def _success_url(request: Request) -> str:
    explicit = os.getenv("BILLING_SUCCESS_URL", "").strip()
    return explicit or f"{_base_url(request)}/?billing=success&session_id={{CHECKOUT_SESSION_ID}}"


def _cancel_url(request: Request) -> str:
    explicit = os.getenv("BILLING_CANCEL_URL", "").strip()
    return explicit or f"{_base_url(request)}/?billing=cancel"


def _portal_return_url(request: Request) -> str:
    return os.getenv("BILLING_PORTAL_RETURN_URL", "").strip() or f"{_base_url(request)}/"


class CheckoutBody(BaseModel):
    plan: str  # "monthly" or "yearly"


class VerifySessionBody(BaseModel):
    session_id: str


@router.post("/api/billing/checkout")
async def create_checkout(body: CheckoutBody, request: Request, user: dict = Depends(require_member)):
    if body.plan not in (PLAN_MONTHLY, PLAN_YEARLY):
        raise HTTPException(status_code=400, detail="Invalid plan")
    _stripe_key()
    price_id = _price_id(body.plan)

    db_user = await get_user_by_id(user["uid"])
    if not db_user:
        raise HTTPException(status_code=401, detail="User not found")

    # Reuse an existing Stripe customer if we've seen one for this user;
    # otherwise let Checkout create one tied to their email.
    customer_kwargs = {}
    if db_user.get("stripe_customer_id"):
        customer_kwargs["customer"] = db_user["stripe_customer_id"]
    else:
        customer_kwargs["customer_email"] = db_user["email"]

    subscription_data: dict = {"metadata": {"user_id": str(db_user["id"])}}
    trial_days = _trial_days()
    if trial_days > 0:
        subscription_data["trial_period_days"] = trial_days

    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=_success_url(request),
            cancel_url=_cancel_url(request),
            client_reference_id=str(db_user["id"]),
            metadata={"user_id": str(db_user["id"]), "plan": body.plan},
            subscription_data=subscription_data,
            allow_promotion_codes=True,
            **customer_kwargs,
        )
    except stripe.error.StripeError as exc:
        logger.exception("Stripe checkout create failed")
        raise HTTPException(status_code=502, detail=str(exc))

    return {"url": session.url, "session_id": session.id}


@router.post("/api/billing/portal")
async def create_portal(request: Request, user: dict = Depends(require_member)):
    _stripe_key()
    db_user = await get_user_by_id(user["uid"])
    if not db_user or not db_user.get("stripe_customer_id"):
        raise HTTPException(status_code=400, detail="No active billing account — subscribe first")
    try:
        portal = await asyncio.to_thread(
            stripe.billing_portal.Session.create,
            customer=db_user["stripe_customer_id"],
            return_url=_portal_return_url(request),
        )
    except stripe.error.StripeError as exc:
        logger.exception("Stripe portal create failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"url": portal.url}


# ── Synchronous activation on redirect back from Checkout ─────────────────
# The webhook is the durable source of truth, but it can land 5–15s after the
# user returns. Verifying the session directly with Stripe on redirect makes
# Pro flip on the next /me poll instead of waiting on the webhook.

@router.post("/api/billing/verify-session")
async def verify_session(body: VerifySessionBody, user: dict = Depends(require_member)):
    _stripe_key()
    session_id = (body.session_id or "").strip()
    if not session_id or not session_id.startswith("cs_"):
        raise HTTPException(status_code=400, detail="Invalid session_id")

    try:
        session = await asyncio.to_thread(stripe.checkout.Session.retrieve, session_id)
    except stripe.error.StripeError as exc:
        logger.exception("verify-session retrieve failed")
        raise HTTPException(status_code=502, detail=str(exc))

    # The session must belong to the caller — defense against guessing/replay.
    ref = session.get("client_reference_id")
    if str(ref or "") != str(user["uid"]):
        raise HTTPException(status_code=403, detail="Session does not belong to this user")

    if session.get("payment_status") != "paid":
        return {"ok": False, "payment_status": session.get("payment_status")}

    await _apply_checkout_session(session)
    pro_until = await get_user_pro_until(user["uid"])
    return {
        "ok": True,
        "pro_until": pro_until.isoformat() if pro_until else None,
    }


# ── Webhook ────────────────────────────────────────────────────────────────

def _ts_to_dt(ts) -> Optional[dt.datetime]:
    if ts is None:
        return None
    try:
        return dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


async def _apply_subscription_state(subscription: dict) -> None:
    """Mirror a Stripe Subscription object into users.pro_until and our IDs."""
    user_id: Optional[int] = None
    metadata = subscription.get("metadata") or {}
    raw_uid = metadata.get("user_id")
    if raw_uid:
        try:
            user_id = int(raw_uid)
        except (TypeError, ValueError):
            user_id = None
    customer_id = subscription.get("customer")
    if user_id is None and customer_id:
        u = await get_user_by_stripe_customer(customer_id)
        if u:
            user_id = u["id"]
    if user_id is None:
        logger.warning("Stripe subscription event with no resolvable user (cust=%s sub=%s)",
                       customer_id, subscription.get("id"))
        return

    status = subscription.get("status")
    period_end = _ts_to_dt(subscription.get("current_period_end"))
    cancel_at = _ts_to_dt(subscription.get("cancel_at"))

    # Active-ish statuses keep entitlement until period_end.
    # canceled / unpaid / incomplete_expired drop entitlement immediately
    # (Stripe sends a separate `customer.subscription.deleted` for hard cancel).
    granting_statuses = {"trialing", "active", "past_due"}
    if status in granting_statuses and period_end:
        # If cancel_at is set and earlier than period_end, prefer it
        effective_end = period_end
        if cancel_at and cancel_at < period_end:
            effective_end = cancel_at
        await set_user_pro_until(user_id, effective_end)
    else:
        await set_user_pro_until(user_id, None)

    await set_user_stripe_ids(user_id, customer_id, subscription.get("id"))
    logger.info("Stripe sub sync: user %d status=%s pro_until=%s",
                user_id, status, period_end.isoformat() if period_end else None)


async def _apply_checkout_session(session: dict) -> None:
    user_id: Optional[int] = None
    ref = session.get("client_reference_id")
    if ref:
        try:
            user_id = int(ref)
        except (TypeError, ValueError):
            user_id = None
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    if user_id is None and customer_id:
        u = await get_user_by_stripe_customer(customer_id)
        if u:
            user_id = u["id"]
    if user_id is None:
        logger.warning("Stripe checkout.completed with no user ref (sub=%s)", subscription_id)
        return

    # Persist IDs early so the portal endpoint works even before the next
    # subscription.updated event lands.
    await set_user_stripe_ids(user_id, customer_id, subscription_id)

    if subscription_id:
        try:
            sub = await asyncio.to_thread(stripe.Subscription.retrieve, subscription_id)
        except stripe.error.StripeError:
            logger.exception("Failed to fetch subscription %s", subscription_id)
            return
        await _apply_subscription_state(sub)


@router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: Optional[str] = Header(None)):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        logger.error("STRIPE_WEBHOOK_SECRET not set — refusing webhook")
        raise HTTPException(status_code=503, detail="Webhook not configured")
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing signature")

    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid webhook: {exc}")

    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed":
        await _apply_checkout_session(data_object)
    elif event_type in ("customer.subscription.created",
                        "customer.subscription.updated",
                        "customer.subscription.resumed",
                        "customer.subscription.paused"):
        await _apply_subscription_state(data_object)
    elif event_type == "customer.subscription.deleted":
        # Hard cancel — drop entitlement immediately.
        user_id = None
        metadata = data_object.get("metadata") or {}
        raw_uid = metadata.get("user_id")
        if raw_uid:
            try:
                user_id = int(raw_uid)
            except (TypeError, ValueError):
                user_id = None
        if user_id is None:
            cust = data_object.get("customer")
            if cust:
                u = await get_user_by_stripe_customer(cust)
                if u:
                    user_id = u["id"]
        if user_id is not None:
            await set_user_pro_until(user_id, None)
            await set_user_stripe_ids(user_id, data_object.get("customer"), None)
            logger.info("Stripe sub deleted: user %d", user_id)
    else:
        logger.debug("Stripe event ignored: %s", event_type)

    return {"ok": True}


# ── Beta codes ─────────────────────────────────────────────────────────────

class RedeemBody(BaseModel):
    code: str


@router.post("/api/billing/redeem")
async def redeem_beta_code(body: RedeemBody, user: dict = Depends(require_member)):
    code = (body.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Enter a code")

    async with get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT code, duration_days, redeemed_by_user_id FROM beta_codes WHERE code=$1 FOR UPDATE",
                code,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Invalid code")
            if row["redeemed_by_user_id"] is not None:
                raise HTTPException(status_code=400, detail="This code has already been redeemed")

            duration_days = int(row["duration_days"])
            now = dt.datetime.now(dt.timezone.utc)
            current = await get_user_pro_until(user["uid"])
            base = current if (current and current > now) else now
            new_until = base + dt.timedelta(days=duration_days)

            await conn.execute(
                "UPDATE users SET pro_until=$1 WHERE id=$2",
                new_until, user["uid"],
            )
            await conn.execute(
                "UPDATE beta_codes SET redeemed_by_user_id=$1, redeemed_at=NOW() WHERE code=$2",
                user["uid"], code,
            )

    logger.info("Beta code redeemed: user %d code %s (+%d days)", user["uid"], code, duration_days)
    return {"ok": True, "pro_until": new_until.isoformat(), "duration_days": duration_days}


def _generate_code() -> str:
    # 12-char alphanumeric, no ambiguous chars, formatted XXXX-XXXX-XXXX.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


class MintBody(BaseModel):
    count: int = 1
    duration_days: int = BETA_CODE_DURATION_DAYS
    note: Optional[str] = None


@router.post("/api/admin/beta-codes")
async def admin_mint_codes(body: MintBody, user: dict = Depends(require_admin)):
    if body.count < 1 or body.count > 500:
        raise HTTPException(status_code=400, detail="count must be 1–500")
    if body.duration_days < 1 or body.duration_days > 3650:
        raise HTTPException(status_code=400, detail="duration_days must be 1–3650")

    minted: list[str] = []
    async with get_pool().acquire() as conn:
        attempts = 0
        while len(minted) < body.count and attempts < body.count * 4:
            attempts += 1
            code = _generate_code()
            try:
                await conn.execute(
                    "INSERT INTO beta_codes (code, duration_days, note) VALUES ($1, $2, $3)",
                    code, body.duration_days, body.note,
                )
                minted.append(code)
            except Exception:
                # Collision on PK — try again.
                continue
    if len(minted) < body.count:
        logger.warning("Beta code mint short: requested %d, got %d", body.count, len(minted))
    return {"codes": minted, "duration_days": body.duration_days, "note": body.note}


@router.get("/api/admin/beta-codes")
async def admin_list_codes(user: dict = Depends(require_admin), limit: int = 200):
    limit = max(1, min(limit, 1000))
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT b.code, b.duration_days, b.note, b.created_at,
                      b.redeemed_at, b.redeemed_by_user_id, u.email AS redeemed_by_email
               FROM beta_codes b
               LEFT JOIN users u ON u.id = b.redeemed_by_user_id
               ORDER BY b.created_at DESC
               LIMIT $1""",
            limit,
        )
    return {
        "codes": [
            {
                "code": r["code"],
                "duration_days": r["duration_days"],
                "note": r["note"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "redeemed_at": r["redeemed_at"].isoformat() if r["redeemed_at"] else None,
                "redeemed_by_user_id": r["redeemed_by_user_id"],
                "redeemed_by_email": r["redeemed_by_email"],
            }
            for r in rows
        ]
    }
