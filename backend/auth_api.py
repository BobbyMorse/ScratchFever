"""
Auth endpoints: login, register, me.
"""
import re
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.users import (
    create_user, get_user_by_email, verify_password,
    create_token, require_member, is_user_pro,
    get_user_prefs, set_user_prefs, get_user_by_id,
)
from backend import analytics

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
    return {"token": token, "email": user["email"], "username": user.get("username"), "role": user["role"]}


@router.get("/api/auth/me")
async def get_me(user: dict = Depends(require_member)):
    db_user = await get_user_by_id(user["uid"]) or {}
    pro_until = db_user.get("pro_until")
    import datetime as _dt
    is_pro = bool(pro_until and pro_until > _dt.datetime.now(_dt.timezone.utc))
    prefs = await get_user_prefs(user["uid"])
    return {
        "id": user["uid"],
        "email": user["email"],
        "username": user.get("username"),
        "role": user["role"],
        "is_pro": is_pro,
        "pro_until": pro_until.isoformat() if pro_until else None,
        "has_stripe": bool(db_user.get("stripe_customer_id")),
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


@router.get("/api/auth/prefs")
async def get_prefs(user: dict = Depends(require_member)):
    return await get_user_prefs(user["uid"])


@router.put("/api/auth/prefs")
async def put_prefs(body: dict, user: dict = Depends(require_member)):
    current = await get_user_prefs(user["uid"])
    merged = {**current, **_sanitize_prefs(body)}
    await set_user_prefs(user["uid"], merged)
    return merged
