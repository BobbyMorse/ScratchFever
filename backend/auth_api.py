"""
Auth endpoints: login, register, me.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.users import (
    create_user, get_user_by_email, verify_password,
    create_token, require_member,
)

router = APIRouter()

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.@+\-]{3,64}$")


class LoginBody(BaseModel):
    email: str
    password: str


class RegisterBody(BaseModel):
    email: str
    username: str
    password: str


@router.post("/api/auth/login")
async def login(body: LoginBody):
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
