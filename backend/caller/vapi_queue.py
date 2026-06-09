"""
Concurrency-limited VAPI dispatch queue.

Problem: POSTing to VAPI's /call returns in ~200ms regardless of whether the
actual phone call completes. The old _dispatch_calls path used a Semaphore to
limit POST concurrency, which meant 100 retailers → 100 simultaneous live calls
on Twilio within a few seconds — instant robocall-flag territory on a single
BYO number.

Fix: persist pending dispatches in vapi_call_queue, gate the dispatch loop on
ACTIVE call count (vapi_calls.ended_at IS NULL), not POST concurrency. Default
ceiling is 2 simultaneous live calls — humane-looking pace for one Twilio
number, and we can bump it if more numbers come online.

Survives Railway redeploys: the queue is a DB table, so pushing code mid-batch
just resumes from the next pending row.
"""
from __future__ import annotations
import asyncio
import datetime as dt
import json
import logging
import os
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.database import get_pool, add_column_if_missing
from backend.users import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vapi/queue", tags=["vapi-queue"])


# ── Tuning knobs ────────────────────────────────────────────────────────────

# Hard ceiling on simultaneous LIVE calls (counted from vapi_calls rows with
# ended_at IS NULL). 2 is the sweet spot for one BYO Twilio long-code: stays
# under the "robocall pattern" detection threshold most carriers use while
# still giving ~160 calls/hour throughput at average 45s/call.
DEFAULT_MAX_CONCURRENT = int(os.getenv("VAPI_MAX_CONCURRENT_CALLS", "2"))

# How often the worker wakes to check whether a slot opened up. 5s is short
# enough that we backfill slots promptly when a call ends, long enough that
# we're not hammering Postgres.
WORKER_POLL_SEC = float(os.getenv("VAPI_QUEUE_POLL_SEC", "5"))

# "In-flight" cutoff: a vapi_calls row with no ended_at older than this is
# considered stuck (webhook never arrived), so we stop letting it block new
# dispatches. Matches the existing /api/vapi/stats in_flight definition.
IN_FLIGHT_STALE_MIN = 15

# Mutable runtime overrides — set via POST /api/vapi/queue/concurrency and
# POST /api/vapi/queue/pause so an admin can throttle without redeploying.
_RUNTIME_MAX_CONCURRENT: Optional[int] = None
_PAUSED: bool = False
_RUNTIME_LOCK = asyncio.Lock()


def get_max_concurrent() -> int:
    return _RUNTIME_MAX_CONCURRENT if _RUNTIME_MAX_CONCURRENT is not None else DEFAULT_MAX_CONCURRENT


# ── Schema ──────────────────────────────────────────────────────────────────

async def init_vapi_queue_db() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS vapi_call_queue (
                id SERIAL PRIMARY KEY,
                batch_id UUID NOT NULL,
                enqueued_at TIMESTAMPTZ DEFAULT NOW(),
                enqueued_by_user_id INTEGER,

                state_code TEXT NOT NULL,
                retailer_external_id TEXT,
                retailer_name TEXT,
                retailer_city TEXT,
                to_phone TEXT NOT NULL,

                tickets JSONB NOT NULL,
                force_phone_number_id TEXT,

                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                dispatched_at TIMESTAMPTZ,
                vapi_call_id TEXT,
                last_error TEXT
            )
        """)
        await add_column_if_missing(conn, "vapi_call_queue", "enqueued_by_user_id", "INTEGER")
        await add_column_if_missing(conn, "vapi_call_queue", "force_phone_number_id", "TEXT")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vapi_queue_pending "
            "ON vapi_call_queue(enqueued_at) WHERE status = 'pending'"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vapi_queue_batch ON vapi_call_queue(batch_id)"
        )


# ── Enqueue ─────────────────────────────────────────────────────────────────

async def enqueue_targets(
    targets: list[dict],
    tickets: list[dict],
    *,
    force_phone_number_id: Optional[str] = None,
    enqueued_by_user_id: Optional[int] = None,
) -> dict:
    """Bulk-insert pending queue rows for a batch. Targets must already have
    a valid E.164 phone in `to_phone` — call _to_e164 before passing in.

    Returns {batch_id, enqueued} so the caller can show the batch in the UI."""
    batch_id = str(uuid.uuid4())
    if not targets:
        return {"batch_id": batch_id, "enqueued": 0}

    tickets_json = json.dumps(tickets)
    pool = get_pool()
    inserted = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for t in targets:
                await conn.execute(
                    """
                    INSERT INTO vapi_call_queue (
                        batch_id, enqueued_by_user_id,
                        state_code, retailer_external_id, retailer_name, retailer_city,
                        to_phone, tickets, force_phone_number_id, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, 'pending')
                    """,
                    batch_id, enqueued_by_user_id,
                    (t.get("state_code") or "").upper(),
                    t.get("external_id") or "",
                    t.get("name") or "",
                    t.get("city") or "",
                    t["to_phone"],
                    tickets_json,
                    force_phone_number_id,
                )
                inserted += 1
    return {"batch_id": batch_id, "enqueued": inserted}


# ── Capacity check ──────────────────────────────────────────────────────────

async def _count_in_flight(conn) -> int:
    """Active live calls — POSTed but webhook hasn't marked them ended. The
    15-min cutoff prevents a single stuck row from permanently blocking the
    queue (matches the /stats in_flight definition)."""
    return await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM vapi_calls
        WHERE ended_at IS NULL
          AND COALESCE(started_at, received_at) > NOW() - ($1::int || ' min')::interval
          AND COALESCE(retailer_external_id, '') <> 'test'
        """,
        IN_FLIGHT_STALE_MIN,
    ) or 0


async def _count_dispatching(conn) -> int:
    """Queue rows currently mid-POST. Should be tiny but counts as occupied
    so we don't briefly over-dispatch between POST and webhook arrival."""
    return await conn.fetchval(
        "SELECT COUNT(*) FROM vapi_call_queue WHERE status = 'dispatching'"
    ) or 0


# ── Worker loop ─────────────────────────────────────────────────────────────

async def vapi_queue_worker_loop() -> None:
    """Background task: every WORKER_POLL_SEC, claim and dispatch as many
    pending rows as we have free slots. SELECT FOR UPDATE SKIP LOCKED makes
    it safe to run multiple worker instances (we currently run one)."""
    logger.info(
        "vapi_queue worker started — max_concurrent=%d poll=%.1fs",
        get_max_concurrent(), WORKER_POLL_SEC,
    )
    while True:
        try:
            if _PAUSED:
                await asyncio.sleep(WORKER_POLL_SEC)
                continue
            await _tick()
        except asyncio.CancelledError:
            logger.info("vapi_queue worker cancelled")
            raise
        except Exception:
            logger.exception("vapi_queue worker tick failed")
        await asyncio.sleep(WORKER_POLL_SEC)


async def _tick() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        in_flight    = await _count_in_flight(conn)
        dispatching  = await _count_dispatching(conn)
    occupied = in_flight + dispatching
    cap = get_max_concurrent()
    free = cap - occupied
    if free <= 0:
        return

    # Claim up to `free` pending rows atomically. SKIP LOCKED + status flip
    # in one transaction = no double-dispatch.
    claimed = await _claim_pending(free)
    if not claimed:
        return

    logger.info(
        "vapi_queue dispatching %d (in_flight=%d, dispatching=%d, cap=%d)",
        len(claimed), in_flight, dispatching, cap,
    )
    # Fire each claim concurrently — they each correspond to one real call, so
    # this respects the cap by construction (we only claimed `free` rows).
    await asyncio.gather(*(_dispatch_one(row) for row in claimed))


async def _claim_pending(limit: int) -> list[dict]:
    """Atomically grab the next `limit` pending rows and mark them
    'dispatching'. SKIP LOCKED so concurrent workers don't fight."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, batch_id, state_code, retailer_external_id, retailer_name,
                       retailer_city, to_phone, tickets, force_phone_number_id, attempts
                FROM vapi_call_queue
                WHERE status = 'pending'
                ORDER BY enqueued_at ASC, id ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
                """,
                limit,
            )
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            await conn.execute(
                "UPDATE vapi_call_queue SET status = 'dispatching', attempts = attempts + 1 "
                "WHERE id = ANY($1::int[])",
                ids,
            )
    return [dict(r) for r in rows]


async def _dispatch_one(row: dict) -> None:
    """Call the existing vapi_dispatch primitive for one queued row, then
    update the row's status based on the result."""
    # Local import to dodge the vapi_dispatch ↔ vapi_queue cycle.
    from backend.caller.vapi_dispatch import _dispatch_calls, _vapi_env

    env = _vapi_env()
    if not env.get("private_key") or not env.get("assistant_id"):
        await _mark_failed(row["id"], "VAPI not configured (missing private_key/assistant_id)")
        return

    tickets_raw = row["tickets"]
    if isinstance(tickets_raw, str):
        try:
            tickets = json.loads(tickets_raw)
        except Exception:
            tickets = []
    else:
        tickets = tickets_raw or []

    target = {
        "external_id": row["retailer_external_id"] or "",
        "state_code":  row["state_code"] or "",
        "name":        row["retailer_name"] or "",
        "city":        row["retailer_city"] or "",
        # _dispatch_calls re-runs _to_e164; passing the already-E.164 string
        # back through is a no-op.
        "phone":       row["to_phone"],
    }

    try:
        results, _skipped = await _dispatch_calls(
            [target], tickets, env,
            force_phone_number_id=row.get("force_phone_number_id"),
        )
    except Exception as exc:
        logger.exception("queue dispatch crashed for id=%s", row["id"])
        await _mark_failed(row["id"], f"crash: {exc}")
        return

    r = results[0] if results else None
    if r and r.get("ok"):
        await _mark_dispatched(row["id"], r.get("call_id"))
    else:
        err = (r or {}).get("error") or "unknown error"
        await _mark_failed(row["id"], err)


async def _mark_dispatched(queue_id: int, vapi_call_id: Optional[str]) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE vapi_call_queue
               SET status='dispatched', dispatched_at = NOW(), vapi_call_id = $2, last_error = NULL
               WHERE id = $1""",
            queue_id, vapi_call_id,
        )


async def _mark_failed(queue_id: int, error: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE vapi_call_queue
               SET status='failed', dispatched_at = NOW(), last_error = $2
               WHERE id = $1""",
            queue_id, (error or "")[:500],
        )


# ── Admin endpoints ─────────────────────────────────────────────────────────

@router.get("/stats")
async def queue_stats(_user: dict = Depends(require_admin)):
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM vapi_call_queue GROUP BY status"
        )
        in_flight   = await _count_in_flight(conn)
        dispatching = await _count_dispatching(conn)
        # Oldest pending — useful "next up in N seconds" hint in the UI.
        oldest_pending = await conn.fetchval(
            "SELECT MIN(enqueued_at) FROM vapi_call_queue WHERE status='pending'"
        )
    by_status = {r["status"]: int(r["n"]) for r in rows}
    return {
        "pending":         by_status.get("pending", 0),
        "dispatching":     by_status.get("dispatching", 0),
        "dispatched":      by_status.get("dispatched", 0),
        "failed":          by_status.get("failed", 0),
        "cancelled":       by_status.get("cancelled", 0),
        "in_flight_calls": int(in_flight),
        "occupied_slots":  int(in_flight + dispatching),
        "max_concurrent":  get_max_concurrent(),
        "paused":          _PAUSED,
        "oldest_pending":  oldest_pending.isoformat() if oldest_pending else None,
        "poll_sec":        WORKER_POLL_SEC,
    }


@router.get("/list")
async def queue_list(
    _user: dict = Depends(require_admin),
    status: Optional[str] = Query(None, description="pending|dispatching|dispatched|failed|cancelled"),
    batch_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    clauses = []
    args: list = []
    if status:
        args.append(status)
        clauses.append(f"status = ${len(args)}")
    if batch_id:
        args.append(batch_id)
        clauses.append(f"batch_id = ${len(args)}::uuid")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    args.append(limit)
    sql = f"""
        SELECT id, batch_id, enqueued_at, status, attempts, dispatched_at,
               state_code, retailer_external_id, retailer_name, retailer_city,
               to_phone, vapi_call_id, last_error
        FROM vapi_call_queue
        {where}
        ORDER BY enqueued_at DESC, id DESC
        LIMIT ${len(args)}
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return {"items": [dict(r) | {"batch_id": str(r["batch_id"])} for r in rows], "count": len(rows)}


class ConcurrencyBody(BaseModel):
    max_concurrent: int = Field(..., ge=1, le=20)


@router.post("/concurrency")
async def queue_set_concurrency(body: ConcurrencyBody, _user: dict = Depends(require_admin)):
    global _RUNTIME_MAX_CONCURRENT
    async with _RUNTIME_LOCK:
        _RUNTIME_MAX_CONCURRENT = body.max_concurrent
    logger.info("vapi_queue max_concurrent set to %d", body.max_concurrent)
    return {"max_concurrent": get_max_concurrent()}


@router.post("/pause")
async def queue_pause(_user: dict = Depends(require_admin)):
    global _PAUSED
    async with _RUNTIME_LOCK:
        _PAUSED = True
    return {"paused": True}


@router.post("/resume")
async def queue_resume(_user: dict = Depends(require_admin)):
    global _PAUSED
    async with _RUNTIME_LOCK:
        _PAUSED = False
    return {"paused": False}


class CancelBody(BaseModel):
    ids: Optional[list[int]] = None
    batch_id: Optional[str] = None
    all_pending: bool = False


@router.post("/cancel")
async def queue_cancel(body: CancelBody, _user: dict = Depends(require_admin)):
    """Cancel pending rows only — already-dispatched calls can't be unsent."""
    if not (body.ids or body.batch_id or body.all_pending):
        raise HTTPException(status_code=400, detail="Provide ids, batch_id, or all_pending=true")
    pool = get_pool()
    async with pool.acquire() as conn:
        if body.all_pending:
            res = await conn.execute(
                "UPDATE vapi_call_queue SET status='cancelled' WHERE status='pending'"
            )
        elif body.batch_id:
            res = await conn.execute(
                "UPDATE vapi_call_queue SET status='cancelled' "
                "WHERE status='pending' AND batch_id = $1::uuid",
                body.batch_id,
            )
        else:
            res = await conn.execute(
                "UPDATE vapi_call_queue SET status='cancelled' "
                "WHERE status='pending' AND id = ANY($1::int[])",
                body.ids,
            )
    # asyncpg returns "UPDATE N" — parse the count for the response.
    try:
        cancelled = int(res.rsplit(" ", 1)[-1])
    except Exception:
        cancelled = 0
    return {"cancelled": cancelled}
