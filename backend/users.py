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

from backend.database import get_pool

logger = logging.getLogger(__name__)

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
        # Mirror of the RevenueCat "pro" entitlement, updated by the RC webhook.
        # NULL or past = not Pro; future = Pro until that timestamp.
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pro_until TIMESTAMPTZ")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS prefs JSONB NOT NULL DEFAULT '{}'::jsonb")


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


async def is_user_pro(user_id: int) -> bool:
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
    """Called by the RevenueCat webhook. `pro_until` may be a datetime or None."""
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET pro_until=$1 WHERE id=$2",
            pro_until, user_id,
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


def create_token(user_id: int, email: str, role: str, username: str = None) -> str:
    payload = json.dumps(
        {"uid": user_id, "email": email, "username": username, "role": role,
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
