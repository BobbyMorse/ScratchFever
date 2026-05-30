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
    create_token, require_member,
)

router = APIRouter()

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.@+\-]{3,64}$")

# Per-IP sliding-window throttle for /api/auth/login.
# Blocks credential-stuffing without locking out users behind shared NAT.
_LOGIN_WINDOW_SEC = 60
_LOGIN_MAX_PER_WINDOW = 10
_login_attempts: dict[str, deque[float]] = {}


def _check_login_rate(ip: str) -> None:
    now = time.monotonic()
    cutoff = now - _LOGIN_WINDOW_SEC
    dq = _login_attempts.setdefault(ip, deque())
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= _LOGIN_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in a minute.")
    dq.append(now)
    if len(_login_attempts) > 10000:
        # Bound memory under attack: drop the oldest half of tracked IPs.
        for k in list(_login_attempts.keys())[:5000]:
            _login_attempts.pop(k, None)


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
    token = create_token(user["id"], user["email"], user["role"], user.get("username"))
    return {"token": token, "email": user["email"], "username": user.get("username"), "role": user["role"]}


@router.post("/api/auth/register")
async def register(body: RegisterBody):
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
    return {"email": user["email"], "username": user.get("username"), "role": user["role"]}
