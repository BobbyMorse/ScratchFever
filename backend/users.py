"""
User auth — password hashing (PBKDF2) and HMAC-signed tokens.
No external JWT dependencies; stdlib only.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Optional

import aiosqlite
from fastapi import Header, HTTPException

from backend.database import DB_PATH

logger = logging.getLogger(__name__)

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    username TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    created_at TEXT DEFAULT (datetime('now'))
);
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


# ── Users DB ──────────────────────────────────────────────────────────────────

async def init_users_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(USERS_SCHEMA)
        for col in ["username"]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
                await db.commit()
            except Exception:
                pass
        try:
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username IS NOT NULL"
            )
            await db.commit()
        except Exception:
            pass
        await db.commit()


async def create_user(email: str, password: str, role: str = "member", username: str = None) -> dict:
    pw_hash = _hash_password(password)
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cursor = await db.execute(
                "INSERT INTO users (email, username, password_hash, role) VALUES (?, ?, ?, ?)",
                (email.lower().strip(), username, pw_hash, role),
            )
            await db.commit()
            return {"id": cursor.lastrowid, "email": email.lower().strip(), "username": username, "role": role}
        except Exception as exc:
            if "UNIQUE" in str(exc):
                if "username" in str(exc).lower():
                    raise ValueError("Username already taken")
                raise ValueError("Email already registered")
            raise


async def get_user_by_email(email: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, email, username, password_hash, role FROM users WHERE email=?",
            (email.lower().strip(),),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def seed_admin():
    """Create admin from env vars if not already present."""
    email    = os.getenv("ADMIN_EMAIL", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not email or not password:
        logger.warning("ADMIN_EMAIL/ADMIN_PASSWORD not set — no admin seeded")
        return
    existing = await get_user_by_email(email)
    if existing:
        if existing["role"] != "admin":
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET role='admin' WHERE email=?", (email,))
                await db.commit()
            logger.info("Promoted %s to admin", email)
        return
    await create_user(email, password, role="admin")
    logger.info("Admin account created: %s", email)


# ── Password hashing (PBKDF2-SHA256) ─────────────────────────────────────────

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


# ── Token creation / verification ────────────────────────────────────────────

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


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def _token_from_header(authorization: str = Header(None)) -> Optional[str]:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
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
