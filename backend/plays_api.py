"""
My Plays — personal win/loss tracker endpoints.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.database import get_pool
from backend.users import require_member

router = APIRouter()


class LogPlayBody(BaseModel):
    game_name: str
    game_db_id: Optional[int] = None
    state_code: Optional[str] = None
    price_paid: float
    prize_won: float = 0.0
    retailer_name: Optional[str] = None
    notes: Optional[str] = None
    played_at: Optional[str] = None


@router.get("/api/plays")
async def get_plays(user: dict = Depends(require_member)):
    uid = user["uid"]
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, game_name, game_db_id, state_code, price_paid, prize_won,
                      retailer_name, notes, played_at
               FROM user_plays
               WHERE user_id = $1
               ORDER BY played_at DESC
               LIMIT 500""",
            uid,
        )
    plays = [
        {
            "id": r["id"],
            "game_name": r["game_name"],
            "game_db_id": r["game_db_id"],
            "state_code": r["state_code"],
            "price_paid": r["price_paid"],
            "prize_won": r["prize_won"],
            "retailer_name": r["retailer_name"],
            "notes": r["notes"],
            "played_at": r["played_at"].isoformat() if r["played_at"] else None,
        }
        for r in rows
    ]
    return {"plays": plays}


@router.post("/api/plays")
async def log_play(body: LogPlayBody, user: dict = Depends(require_member)):
    if body.price_paid <= 0:
        raise HTTPException(status_code=400, detail="price_paid must be positive")
    if body.prize_won < 0:
        raise HTTPException(status_code=400, detail="prize_won cannot be negative")

    played_at = None
    if body.played_at:
        try:
            played_at = dt.datetime.fromisoformat(body.played_at.replace("Z", "+00:00"))
        except ValueError:
            played_at = None

    uid = user["uid"]
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO user_plays
               (user_id, game_name, game_db_id, state_code, price_paid, prize_won,
                retailer_name, notes, played_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,COALESCE($9,NOW()))
               RETURNING id, played_at""",
            uid,
            body.game_name.strip(),
            body.game_db_id,
            body.state_code.upper().strip() if body.state_code else None,
            body.price_paid,
            body.prize_won,
            body.retailer_name.strip() if body.retailer_name else None,
            body.notes.strip() if body.notes else None,
            played_at,
        )
    return {"id": row["id"], "played_at": row["played_at"].isoformat()}


@router.delete("/api/plays/{play_id}")
async def delete_play(play_id: int, user: dict = Depends(require_member)):
    uid = user["uid"]
    async with get_pool().acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM user_plays WHERE id=$1 AND user_id=$2 RETURNING id",
            play_id, uid,
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Play not found")
    return {"deleted": play_id}
