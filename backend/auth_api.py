"""
Auth endpoints: login, register, me, send-otp, verify-otp.
"""
import logging
import os
import re
import secrets
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.users import (
    create_user, get_user_by_email, verify_password,
    create_token, require_member, is_user_pro,
    get_user_prefs, set_user_prefs, get_user_by_id,
    get_user_by_phone, create_user_by_phone,
    store_phone_otp, consume_phone_otp,
)
from backend.database import get_pool
from backend import analytics

logger = logging.getLogger(__name__)

_ALLOWED_PREF_KEYS = {"defaultHuntState", "evDefaultState"}
_MAX_PREF_VALUE_LEN = 64

router = APIRouter()

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.@+\-]{3,64}$")

# Per-IP sliding-window throttle for /api/auth/login.
# Blocks credential-stuffing without locking out users behind shared NAT.
_LOGIN_WINDOW_SEC = 60
_LOGIN_MAX_PER_WINDOW = 10
_login_attempts: dict[str, deque[float]] = {}

# Tighter throttle for /api/auth/register — legitimate users register once.
_REGISTER_WINDOW_SEC = 3600
_REGISTER_MAX_PER_WINDOW = 5
_register_attempts: dict[str, deque[float]] = {}

# OTP throttles. Every send costs real money via Twilio, so we throttle on
# both axes: by phone (a target can't be spammed) and by IP (one attacker
# can't fan out across many numbers).
_OTP_SEND_PHONE_WINDOW_SEC = 3600
_OTP_SEND_PHONE_MAX = 3
_otp_send_by_phone: dict[str, deque[float]] = {}

_OTP_SEND_IP_WINDOW_SEC = 3600
_OTP_SEND_IP_MAX = 10
_otp_send_by_ip: dict[str, deque[float]] = {}

_OTP_VERIFY_WINDOW_SEC = 60
_OTP_VERIFY_MAX = 10
_otp_verify_by_ip: dict[str, deque[float]] = {}

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def _check_rate(store: dict[str, deque[float]], ip: str, window_sec: int, max_per_window: int, detail: str) -> None:
    now = time.monotonic()
    cutoff = now - window_sec
    dq = store.setdefault(ip, deque())
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= max_per_window:
        raise HTTPException(status_code=429, detail=detail)
    dq.append(now)
    if len(store) > 10000:
        for k in list(store.keys())[:5000]:
            store.pop(k, None)


def _check_login_rate(ip: str) -> None:
    _check_rate(_login_attempts, ip, _LOGIN_WINDOW_SEC, _LOGIN_MAX_PER_WINDOW,
                "Too many login attempts. Try again in a minute.")


def _check_register_rate(ip: str) -> None:
    _check_rate(_register_attempts, ip, _REGISTER_WINDOW_SEC, _REGISTER_MAX_PER_WINDOW,
                "Too many signups from this network. Try again later.")


class LoginBody(BaseModel):
    email: str
    password: str


class RegisterBody(BaseModel):
    email: str
    username: str
    password: str


@router.post("/api/auth/login")
async def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "unknown"
    _check_login_rate(ip)
    user = await get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    from backend.database import get_pool
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_login_at = NOW(), login_count = COALESCE(login_count, 0) + 1 WHERE id = $1",
            user["id"],
        )
    token = create_token(user["id"], user["email"], user["role"], user.get("username"))
    analytics.capture(user["id"], "user_logged_in", {"role": user["role"]})
    return {"token": token, "email": user["email"], "username": user.get("username"), "role": user["role"]}


@router.post("/api/auth/register")
async def register(body: RegisterBody, request: Request):
    ip = request.client.host if request.client else "unknown"
    _check_register_rate(ip)
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    username = body.username.strip()
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Username must be 3–64 characters: letters, numbers, or . _ - + @")
    try:
        user = await create_user(body.email.strip(), body.password, role="member", username=username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    token = create_token(user["id"], user["email"], user["role"], user.get("username"))
    analytics.identify(user["id"], {"email": user["email"], "username": user.get("username"), "role": user["role"]})
    analytics.capture(user["id"], "user_signed_up", {"role": user["role"]})
    return {"token": token, "email": user["email"], "username": user.get("username"), "role": user["role"]}


class SendOtpBody(BaseModel):
    phone: str


class VerifyOtpBody(BaseModel):
    phone: str
    code: str


def _normalize_phone(raw: str) -> str:
    phone = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not _E164_RE.match(phone):
        raise HTTPException(status_code=400, detail="Phone must be E.164 (e.g. +15555551234)")
    return phone


def _send_twilio_sms(phone: str, code: str) -> None:
    """Send the OTP via the same Twilio account used by the retailer caller.
    Raises HTTPException(503) if credentials are unconfigured."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    if not all([account_sid, auth_token, from_number]):
        logger.error("OTP send: Twilio credentials missing")
        raise HTTPException(status_code=503, detail="SMS service unavailable")
    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(account_sid, auth_token)
        client.messages.create(
            to=phone,
            from_=from_number,
            body=f"Your ScratchFrenzy code is {code}. It expires in 10 minutes.",
        )
    except Exception as exc:
        logger.error("OTP send to %s failed: %s", phone, exc)
        raise HTTPException(status_code=502, detail="Could not send code. Try again.")


@router.post("/api/auth/send-otp")
async def send_otp(body: SendOtpBody, request: Request):
    ip = request.client.host if request.client else "unknown"
    phone = _normalize_phone(body.phone)
    _check_rate(_otp_send_by_ip, ip, _OTP_SEND_IP_WINDOW_SEC, _OTP_SEND_IP_MAX,
                "Too many code requests from this network. Try again later.")
    _check_rate(_otp_send_by_phone, phone, _OTP_SEND_PHONE_WINDOW_SEC, _OTP_SEND_PHONE_MAX,
                "Too many codes sent to this number. Try again later.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    await store_phone_otp(phone, code, ttl_seconds=600)
    _send_twilio_sms(phone, code)
    return {"ok": True}


@router.post("/api/auth/verify-otp")
async def verify_otp(body: VerifyOtpBody, request: Request):
    ip = request.client.host if request.client else "unknown"
    _check_rate(_otp_verify_by_ip, ip, _OTP_VERIFY_WINDOW_SEC, _OTP_VERIFY_MAX,
                "Too many verification attempts. Try again in a minute.")
    phone = _normalize_phone(body.phone)
    if not re.match(r"^\d{6}$", body.code or ""):
        raise HTTPException(status_code=400, detail="Code must be 6 digits")
    if not await consume_phone_otp(phone, body.code):
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    existing = await get_user_by_phone(phone)
    if existing:
        user_row = existing
        async with get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_login_at = NOW(), login_count = COALESCE(login_count, 0) + 1 WHERE id = $1",
                user_row["id"],
            )
        analytics.capture(user_row["id"], "user_logged_in", {"role": user_row["role"], "method": "phone"})
    else:
        user_row = await create_user_by_phone(phone)
        analytics.identify(user_row["id"], {"phone": phone, "role": user_row["role"]})
        analytics.capture(user_row["id"], "user_signed_up", {"role": user_row["role"], "method": "phone"})

    token = create_token(user_row["id"], user_row.get("email"), user_row["role"], user_row.get("username"))
    return {
        "token": token,
        "phone": phone,
        "email": user_row.get("email"),
        "username": user_row.get("username"),
        "role": user_row["role"],
    }


@router.get("/api/auth/me")
async def get_me(user: dict = Depends(require_member)):
    db_user = await get_user_by_id(user["uid"]) or {}
    pro_until = db_user.get("pro_until")
    import datetime as _dt
    is_pro = bool(pro_until and pro_until > _dt.datetime.now(_dt.timezone.utc))
    prefs = await get_user_prefs(user["uid"])
    # "My Store" sidebar link is gated on this — role alone isn't enough
    # because admins and freshly-approved retailers may not yet own a profile.
    async with get_pool().acquire() as conn:
        has_store = bool(await conn.fetchval(
            "SELECT 1 FROM retailer_profiles WHERE user_id=$1", user["uid"]
        ))
    return {
        "id": user["uid"],
        "email": db_user.get("email") or "",
        "phone": db_user.get("phone"),
        "username": db_user.get("username") or user.get("username"),
        "role": user["role"],
        "is_pro": is_pro,
        "pro_until": pro_until.isoformat() if pro_until else None,
        "has_stripe": bool(db_user.get("stripe_customer_id")),
        "has_store": has_store,
        "prefs": prefs,
    }


def _sanitize_prefs(raw: dict) -> dict:
    out = {}
    for k, v in (raw or {}).items():
        if k not in _ALLOWED_PREF_KEYS:
            continue
        if v is None:
            out[k] = ""
            continue
        if not isinstance(v, str):
            continue
        if len(v) > _MAX_PREF_VALUE_LEN:
            continue
        out[k] = v
    return out


@router.delete("/api/auth/me")
async def delete_me(user: dict = Depends(require_member)):
    """Delete the authenticated user's account.

    Required by Apple App Store guideline 5.1.1(v) and Google Play
    Account Deletion policy. Most user-linked rows cascade off the users
    FK; inventory_reports stores the username as plain text, so we null
    those fields out by hand so a deleted user's reports become
    anonymous but stay in the aggregate dataset.

    Does NOT cancel any active App Store / Play / Stripe subscription —
    the user must cancel that with the billing platform separately.
    """
    uid = user["uid"]
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE inventory_reports SET reporter_username = NULL, reporter_ip = NULL WHERE reporter_username = $1",
                user.get("username") or "",
            )
            result = await conn.execute("DELETE FROM users WHERE id = $1", uid)
    analytics.capture(uid, "user_deleted_account", {})
    deleted = result.split()[-1] if isinstance(result, str) else ""
    if deleted == "0":
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


@router.get("/api/auth/prefs")
async def get_prefs(user: dict = Depends(require_member)):
    return await get_user_prefs(user["uid"])


@router.put("/api/auth/prefs")
async def put_prefs(body: dict, user: dict = Depends(require_member)):
    current = await get_user_prefs(user["uid"])
    merged = {**current, **_sanitize_prefs(body)}
    await set_user_prefs(user["uid"], merged)
    return merged
