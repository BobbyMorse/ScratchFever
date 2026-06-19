"""
My Tickets — server-side mirror of the mobile app's ticket history.

The mobile client stores tickets locally (AsyncStorage) and works fully
offline. When the user signs in we sync that local history up to the server
so it survives reinstalls and follows them across devices. Conflict
resolution is last-writer-wins by `updated_at`.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.database import get_pool
from backend.users import require_member

router = APIRouter()


class TicketBody(BaseModel):
    # Client-generated UUID; primary sync key.
    client_id: str
    scanned_at: str  # ISO 8601
    game_name: str
    ticket_number: Optional[str] = None
    state: Optional[str] = None
    won: Optional[bool] = None
    prize_amount: Optional[float] = None
    ticket_price: Optional[float] = None
    game_return_pct: Optional[float] = None
    game_top_prize: Optional[float] = None
    game_jackpot_odds_one_in: Optional[float] = None
    game_ev: Optional[float] = None
    game_has_second_chance: Optional[bool] = None
    notes: Optional[str] = None
    raw_ocr_text: Optional[str] = None
    updated_at: Optional[str] = None  # ISO 8601; server defaults to NOW() if missing


class SyncBody(BaseModel):
    tickets: List[TicketBody]


def _parse_iso(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_to_dict(r) -> dict:
    return {
        "client_id": r["client_id"],
        "scanned_at": r["scanned_at"].isoformat() if r["scanned_at"] else None,
        "game_name": r["game_name"],
        "ticket_number": r["ticket_number"],
        "state": r["state"],
        "won": r["won"],
        "prize_amount": r["prize_amount"],
        "ticket_price": r["ticket_price"],
        "game_return_pct": r["game_return_pct"],
        "game_top_prize": r["game_top_prize"],
        "game_jackpot_odds_one_in": r["game_jackpot_odds_one_in"],
        "game_ev": r["game_ev"],
        "game_has_second_chance": r["game_has_second_chance"],
        "notes": r["notes"],
        "raw_ocr_text": r["raw_ocr_text"],
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


async def _upsert(conn, uid: int, t: TicketBody) -> None:
    scanned_at = _parse_iso(t.scanned_at) or dt.datetime.now(dt.timezone.utc)
    updated_at = _parse_iso(t.updated_at) or dt.datetime.now(dt.timezone.utc)
    # Last-writer-wins: only overwrite an existing row when the incoming
    # updated_at is newer than the stored one. Inserts always succeed.
    await conn.execute(
        """
        INSERT INTO user_tickets (
            user_id, client_id, scanned_at, game_name, ticket_number, state,
            won, prize_amount, ticket_price, game_return_pct, game_top_prize,
            game_jackpot_odds_one_in, game_ev, game_has_second_chance,
            notes, raw_ocr_text, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT (user_id, client_id) DO UPDATE SET
            scanned_at = EXCLUDED.scanned_at,
            game_name = EXCLUDED.game_name,
            ticket_number = EXCLUDED.ticket_number,
            state = EXCLUDED.state,
            won = EXCLUDED.won,
            prize_amount = EXCLUDED.prize_amount,
            ticket_price = EXCLUDED.ticket_price,
            game_return_pct = EXCLUDED.game_return_pct,
            game_top_prize = EXCLUDED.game_top_prize,
            game_jackpot_odds_one_in = EXCLUDED.game_jackpot_odds_one_in,
            game_ev = EXCLUDED.game_ev,
            game_has_second_chance = EXCLUDED.game_has_second_chance,
            notes = EXCLUDED.notes,
            raw_ocr_text = EXCLUDED.raw_ocr_text,
            updated_at = EXCLUDED.updated_at
        WHERE user_tickets.updated_at < EXCLUDED.updated_at
        """,
        uid, t.client_id, scanned_at, t.game_name, t.ticket_number, t.state,
        t.won, t.prize_amount, t.ticket_price, t.game_return_pct,
        t.game_top_prize, t.game_jackpot_odds_one_in, t.game_ev,
        t.game_has_second_chance, t.notes, t.raw_ocr_text, updated_at,
    )


@router.get("/api/tickets")
async def list_tickets(user: dict = Depends(require_member)):
    uid = user["uid"]
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT client_id, scanned_at, game_name, ticket_number, state,
                      won, prize_amount, ticket_price, game_return_pct,
                      game_top_prize, game_jackpot_odds_one_in, game_ev,
                      game_has_second_chance, notes, raw_ocr_text, updated_at
               FROM user_tickets
               WHERE user_id = $1
               ORDER BY scanned_at DESC
               LIMIT 5000""",
            uid,
        )
    return {"tickets": [_row_to_dict(r) for r in rows]}


@router.put("/api/tickets/{client_id}")
async def upsert_ticket(client_id: str, body: TicketBody, user: dict = Depends(require_member)):
    if body.client_id != client_id:
        raise HTTPException(status_code=400, detail="client_id mismatch")
    if not body.game_name.strip():
        raise HTTPException(status_code=400, detail="game_name required")
    uid = user["uid"]
    async with get_pool().acquire() as conn:
        await _upsert(conn, uid, body)
    return {"ok": True, "client_id": client_id}


@router.delete("/api/tickets/{client_id}")
async def delete_ticket(client_id: str, user: dict = Depends(require_member)):
    uid = user["uid"]
    async with get_pool().acquire() as conn:
        await conn.execute(
            "DELETE FROM user_tickets WHERE user_id=$1 AND client_id=$2",
            uid, client_id,
        )
    return {"ok": True, "client_id": client_id}


@router.post("/api/tickets/sync")
async def sync_tickets(body: SyncBody, user: dict = Depends(require_member)):
    """Bulk reconcile. Client pushes its full local set; server merges with
    last-writer-wins by updated_at and returns the authoritative list."""
    uid = user["uid"]
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            for t in body.tickets:
                if not t.game_name.strip():
                    continue
                await _upsert(conn, uid, t)
        rows = await conn.fetch(
            """SELECT client_id, scanned_at, game_name, ticket_number, state,
                      won, prize_amount, ticket_price, game_return_pct,
                      game_top_prize, game_jackpot_odds_one_in, game_ev,
                      game_has_second_chance, notes, raw_ocr_text, updated_at
               FROM user_tickets
               WHERE user_id = $1
               ORDER BY scanned_at DESC
               LIMIT 5000""",
            uid,
        )
    return {"tickets": [_row_to_dict(r) for r in rows]}
