"""
User auth — password hashing (PBKDF2) and HMAC-signed tokens.
No external JWT dependencies; stdlib only.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException

from backend.database import get_pool, add_column_if_missing

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() not in ("", "0", "false", "no", "off")


# ── Public mode ─────────────────────────────────────────────────────────────
# Master switch that makes the whole product free & ungated. When on:
#   • every user (and anonymous visitors) is treated as Pro,
#   • Pro-only endpoints degrade to member-level (or public, via optional_member),
#   • clients hide all paywall / upsell / ad chrome (they read this via /api/config).
# All billing/entitlement plumbing (Stripe, RevenueCat, pro_until) is left fully
# intact behind this flag — flip PUBLIC_MODE=0 to restore the paywall unchanged.
# Defaults ON so going public needs no env change; set the env var to monetize.
PUBLIC_MODE = _env_flag("PUBLIC_MODE", True)

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    username TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username IS NOT NULL;
"""

TOKEN_TTL_DAYS = 30
_secret: str | None = None


def _get_secret() -> str:
    global _secret
    if _secret is None:
        _secret = os.getenv("SECRET_KEY", "")
        if not _secret:
            _secret = secrets.token_hex(32)
            logger.warning("SECRET_KEY not set — using ephemeral key, all tokens reset on restart")
    return _secret


async def init_users_db():
    async with get_pool().acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                username TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username IS NOT NULL"
        )
        # Phone-only signups: email + password_hash become nullable so a user
        # can exist with just a verified phone. Existing email users are
        # unaffected — their rows still have both fields populated.
        await add_column_if_missing(conn, "users", "phone", "TEXT")
        await conn.execute("ALTER TABLE users ALTER COLUMN email DROP NOT NULL")
        await conn.execute("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL")
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone) WHERE phone IS NOT NULL"
        )
        # Mirror of the "pro" entitlement. Source of truth is whichever billing
        # provider granted it — Stripe (web) and RevenueCat (mobile) both write here.
        # NULL or past = not Pro; future = Pro until that timestamp.
        await add_column_if_missing(conn, "users", "pro_until", "TIMESTAMPTZ")
        await add_column_if_missing(conn, "users", "prefs", "JSONB NOT NULL DEFAULT '{}'::jsonb")
        # Stripe customer + subscription IDs so the webhook can look users up,
        # and so we can open a Customer Portal session for cancel/update.
        await add_column_if_missing(conn, "users", "stripe_customer_id", "TEXT")
        await add_column_if_missing(conn, "users", "stripe_subscription_id", "TEXT")
        await add_column_if_missing(conn, "users", "last_login_at", "TIMESTAMPTZ")
        await add_column_if_missing(conn, "users", "login_count", "INTEGER NOT NULL DEFAULT 0")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL"
        )
        # Beta codes — one-time-use, grant N days of Pro on redemption.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS beta_codes (
                code TEXT PRIMARY KEY,
                duration_days INTEGER NOT NULL,
                note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                redeemed_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                redeemed_at TIMESTAMPTZ
            )
        """)
        # Phone OTP codes. Phone is PK so a new send overwrites any pending
        # code for the same number — prevents stacking and simplifies cleanup.
        # We store the hash, not the code, so a DB leak can't be used to log in.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS phone_otps (
                phone TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL
            )
        """)


async def create_user(email: str, password: str, role: str = "member", username: str = None) -> dict:
    pw_hash = _hash_password(password)
    async with get_pool().acquire() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO users (email, username, password_hash, role) VALUES ($1, $2, $3, $4) RETURNING id",
                email.lower().strip(), username, pw_hash, role,
            )
            return {"id": row["id"], "email": email.lower().strip(), "username": username, "role": role}
        except Exception as exc:
            msg = str(exc)
            if "unique" in msg.lower() or "duplicate" in msg.lower():
                if "username" in msg.lower():
                    raise ValueError("Username already taken")
                raise ValueError("Email already registered")
            raise


async def get_user_by_email(email: str) -> Optional[dict]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, username, password_hash, role FROM users WHERE email=$1",
            email.lower().strip(),
        )
        return dict(row) if row else None


async def get_user_by_phone(phone: str) -> Optional[dict]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, phone, username, role FROM users WHERE phone=$1",
            phone,
        )
        return dict(row) if row else None


async def create_user_by_phone(phone: str, role: str = "member") -> dict:
    """Create a phone-only user — no email, no password. Used after OTP verify."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO users (phone, role) VALUES ($1, $2) RETURNING id",
            phone, role,
        )
        return {"id": row["id"], "phone": phone, "email": None, "username": None, "role": role}


async def store_phone_otp(phone: str, code: str, ttl_seconds: int = 600) -> None:
    """Hash + store an OTP. Overwrites any prior unverified code for this phone."""
    code_hash = _hash_password(code)
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO phone_otps (phone, code_hash, attempts, created_at, expires_at)
            VALUES ($1, $2, 0, NOW(), NOW() + ($3 || ' seconds')::interval)
            ON CONFLICT (phone) DO UPDATE SET
                code_hash = EXCLUDED.code_hash,
                attempts = 0,
                created_at = NOW(),
                expires_at = EXCLUDED.expires_at
            """,
            phone, code_hash, str(ttl_seconds),
        )


async def consume_phone_otp(phone: str, code: str, max_attempts: int = 5) -> bool:
    """Verify a submitted code. Returns True on match, False otherwise.

    Increments attempt counter on every call. Deletes the row on success
    (one-shot) and on attempt exhaustion (forces a resend).
    """
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT code_hash, attempts, expires_at FROM phone_otps WHERE phone=$1 FOR UPDATE",
                phone,
            )
            if not row:
                return False
            import datetime as _dt
            if row["expires_at"] < _dt.datetime.now(_dt.timezone.utc):
                await conn.execute("DELETE FROM phone_otps WHERE phone=$1", phone)
                return False
            if row["attempts"] >= max_attempts:
                await conn.execute("DELETE FROM phone_otps WHERE phone=$1", phone)
                return False
            if verify_password(code, row["code_hash"]):
                await conn.execute("DELETE FROM phone_otps WHERE phone=$1", phone)
                return True
            await conn.execute(
                "UPDATE phone_otps SET attempts = attempts + 1 WHERE phone=$1",
                phone,
            )
            return False


async def is_user_pro(user_id: int) -> bool:
    if PUBLIC_MODE:
        return True
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT pro_until FROM users WHERE id=$1",
            user_id,
        )
    if not row or row["pro_until"] is None:
        return False
    import datetime as _dt
    return row["pro_until"] > _dt.datetime.now(_dt.timezone.utc)


async def get_user_prefs(user_id: int) -> dict:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT prefs FROM users WHERE id=$1", user_id)
    if not row or row["prefs"] is None:
        return {}
    prefs = row["prefs"]
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except Exception:
            return {}
    return prefs if isinstance(prefs, dict) else {}


async def set_user_prefs(user_id: int, prefs: dict) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET prefs=$1::jsonb WHERE id=$2",
            json.dumps(prefs), user_id,
        )


async def set_user_pro_until(user_id: int, pro_until) -> None:
    """Called by billing webhooks (Stripe, RevenueCat). `pro_until` may be a datetime or None."""
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET pro_until=$1 WHERE id=$2",
            pro_until, user_id,
        )


async def get_user_pro_until(user_id: int):
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT pro_until FROM users WHERE id=$1", user_id)
    return row["pro_until"] if row else None


async def get_user_by_id(user_id: int) -> Optional[dict]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, phone, username, role, stripe_customer_id, stripe_subscription_id, pro_until FROM users WHERE id=$1",
            user_id,
        )
        return dict(row) if row else None


async def get_user_by_stripe_customer(stripe_customer_id: str) -> Optional[dict]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, username, role, stripe_customer_id, stripe_subscription_id, pro_until FROM users WHERE stripe_customer_id=$1",
            stripe_customer_id,
        )
        return dict(row) if row else None


async def set_user_stripe_ids(user_id: int, customer_id: Optional[str], subscription_id: Optional[str]) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET stripe_customer_id=COALESCE($1, stripe_customer_id), stripe_subscription_id=$2 WHERE id=$3",
            customer_id, subscription_id, user_id,
        )


async def seed_admin():
    email    = os.getenv("ADMIN_EMAIL", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not email or not password:
        logger.warning("ADMIN_EMAIL/ADMIN_PASSWORD not set — no admin seeded")
        return
    existing = await get_user_by_email(email)
    if existing:
        if existing["role"] != "admin":
            async with get_pool().acquire() as conn:
                await conn.execute("UPDATE users SET role='admin' WHERE email=$1", email)
            logger.info("Promoted %s to admin", email)
        return
    await create_user(email, password, role="admin")
    logger.info("Admin account created: %s", email)


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key  = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{key.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, stored = password_hash.split(":", 1)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return hmac.compare_digest(key.hex(), stored)
    except Exception:
        return False


def create_token(user_id: int, email: Optional[str], role: str, username: str = None) -> str:
    payload = json.dumps(
        {"uid": user_id, "email": email or "", "username": username, "role": role,
         "exp": int(time.time()) + 86400 * TOKEN_TTL_DAYS},
        separators=(",", ":"),
    )
    b64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    sig = hmac.new(_get_secret().encode(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def decode_token(token: str) -> Optional[dict]:
    try:
        b64, sig = token.rsplit(".", 1)
        expected = hmac.new(_get_secret().encode(), b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padding = "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(b64 + padding))
        if payload["exp"] < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def require_member(authorization: str = Header(None)) -> dict:
    token = authorization[7:] if (authorization or "").startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="Login required")
    user = decode_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token — please log in again")
    return user


def require_admin(authorization: str = Header(None)) -> dict:
    token = authorization[7:] if (authorization or "").startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="Admin login required")
    user = decode_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_pro(authorization: str = Header(None)) -> dict:
    """Pro-only endpoint guard. Authorizes off server-side pro_until,
    not anything in the token — RevenueCat is the source of truth."""
    user = require_member(authorization)
    if user.get("role") == "admin":
        return user
    if not await is_user_pro(user["uid"]):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    return user
