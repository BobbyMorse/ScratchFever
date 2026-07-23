"""Chase requests API.

Pro members upvote tickets they want the caller agent to verify inventory for.
The marketing surface (public-stats) is intentionally aggregate-only — counts
and freshness, no ticket names or EV — so logged-out visitors understand the
shape of the data without seeing the rankings the Pro tier is selling.

Endpoints:
  GET    /api/chase/public-stats          no auth, 60s cached  — aggregate counts
  GET    /api/chase/votes?state_code=…    Pro only             — top-voted games
  POST   /api/chase/request               Pro only             — cast upvote
  DELETE /api/chase/request/{game_db_id}  Pro only             — withdraw upvote
  GET    /api/chase/my-votes              member only          — caller's own votes
  GET    /api/chase/queue                 admin only           — caller routing list
  POST   /api/chase/upgrade-intent        no auth              — funnel event for paywall click
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend import analytics
from backend.database import get_pool
from backend.users import require_admin, require_member, require_pro, optional_member

router = APIRouter(prefix="/api/chase", tags=["chase"])

# In-process cache for the public-stats aggregate. The marketing banner doesn't
# need real-time freshness — a 60s TTL keeps the COUNT-DISTINCT query off the
# critical path even when crawlers hit the landing page in bursts. Keyed by
# state code (or "_ALL_" for the unfiltered total) so each Most Wanted state
# picks up its own cached counts.
_PUBLIC_STATS_TTL = 60.0
_public_stats_cache: dict[str, dict[str, Any]] = {}


@router.get("/public-stats")
async def public_stats(
    state_code: str | None = Query(default=None, max_length=2),
) -> dict[str, Any]:
    sc = (state_code or "").upper().strip() or None
    cache_key = sc or "_ALL_"
    now = time.time()
    entry = _public_stats_cache.get(cache_key)
    if entry and entry["expires_at"] > now:
        return entry["payload"]
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH latest_per_retailer_game AS (
                SELECT DISTINCT ON (retailer_id, state_code, game_name)
                       retailer_id, state_code, game_name, has_stock, reported_at
                  FROM inventory_reports
                 WHERE reported_at > NOW() - INTERVAL '30 days'
                   AND game_name IS NOT NULL
                   AND ($1::text IS NULL OR state_code = $1)
                 ORDER BY retailer_id, state_code, game_name, reported_at DESC
            )
            SELECT
                COUNT(DISTINCT state_code || '|' || game_name)
                  FILTER (WHERE has_stock IS NOT NULL)     AS tracked_count,
                COUNT(*) FILTER (WHERE has_stock = TRUE)   AS stocked_count,
                COUNT(*) FILTER (WHERE has_stock = FALSE)  AS out_count,
                MAX(reported_at)                           AS last_update_at
              FROM latest_per_retailer_game
            """,
            sc,
        )
    payload = {
        "state_code": sc,
        "tracked_count": int(row["tracked_count"] or 0) if row else 0,
        "stocked_count": int(row["stocked_count"] or 0) if row else 0,
        "out_count": int(row["out_count"] or 0) if row else 0,
        "last_update_at": row["last_update_at"].isoformat() if row and row["last_update_at"] else None,
    }
    _public_stats_cache[cache_key] = {"payload": payload, "expires_at": now + _PUBLIC_STATS_TTL}
    return payload


class RequestBody(BaseModel):
    game_db_id: int


@router.post("/request")
async def cast_request(body: RequestBody, user: dict = Depends(require_pro)) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        game = await conn.fetchrow(
            "SELECT id, state_code, name FROM games WHERE id = $1",
            body.game_db_id,
        )
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        new_row = await conn.fetchval(
            """INSERT INTO chase_requests (user_id, game_db_id)
               VALUES ($1, $2)
               ON CONFLICT (user_id, game_db_id) DO NOTHING
               RETURNING id""",
            user["uid"], body.game_db_id,
        )
        vote_count = await conn.fetchval(
            """SELECT COUNT(*) FROM chase_requests
                WHERE game_db_id = $1 AND fulfilled_at IS NULL""",
            body.game_db_id,
        )
    if new_row:
        analytics.capture(user["uid"], "chase_request_submitted", {
            "game_db_id": body.game_db_id,
            "state_code": game["state_code"],
            "game_name": game["name"],
        })
    return {"ok": True, "voted": True, "vote_count": int(vote_count or 0)}


@router.delete("/request/{game_db_id}")
async def withdraw_request(game_db_id: int, user: dict = Depends(require_pro)) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """DELETE FROM chase_requests
                WHERE user_id = $1 AND game_db_id = $2 AND fulfilled_at IS NULL""",
            user["uid"], game_db_id,
        )
        vote_count = await conn.fetchval(
            """SELECT COUNT(*) FROM chase_requests
                WHERE game_db_id = $1 AND fulfilled_at IS NULL""",
            game_db_id,
        )
    return {"ok": True, "voted": False, "vote_count": int(vote_count or 0)}


@router.get("/votes")
async def top_votes(
    state_code: str | None = None,
    limit: int = 50,
    user: dict = Depends(require_pro),
) -> dict[str, Any]:
    """Top games ranked by current open vote count. Optionally filtered to a
    state so the Pro list matches the user's active hunt-state context. Falls
    back to ranking by return_pct when no votes exist yet (cold-start UX)."""
    limit = max(1, min(limit, 200))
    async with get_pool().acquire() as conn:
        # inv_agg = latest has_stock per (state, game, retailer) over the last
        # 30 days, rolled up to in/out counts per (state, game). Matches the
        # public-stats aggregation so the per-game numbers reconcile with the
        # banner totals above the list.
        rows = await conn.fetch("""
            WITH latest_inv AS (
                SELECT DISTINCT ON (state_code, LOWER(game_name), retailer_id)
                       state_code,
                       LOWER(game_name) AS game_name_lc,
                       has_stock
                  FROM inventory_reports
                 WHERE reported_at > NOW() - INTERVAL '30 days'
                   AND game_name IS NOT NULL
                   AND retailer_id IS NOT NULL
                 ORDER BY state_code, LOWER(game_name), retailer_id, reported_at DESC
            ),
            inv_agg AS (
                SELECT state_code,
                       game_name_lc,
                       COUNT(*) FILTER (WHERE has_stock = TRUE)  AS in_count,
                       COUNT(*) FILTER (WHERE has_stock = FALSE) AS out_count
                  FROM latest_inv
                 GROUP BY state_code, game_name_lc
            )
            SELECT g.id                          AS game_db_id,
                   g.state_code,
                   g.name,
                   g.price,
                   g.return_pct,
                   COUNT(cr.id)                  AS vote_count,
                   bool_or(cr.user_id = $1)      AS user_voted,
                   MAX(cr.created_at)            AS last_voted_at,
                   COALESCE(ia.in_count, 0)      AS in_count,
                   COALESCE(ia.out_count, 0)     AS out_count
              FROM games g
              LEFT JOIN chase_requests cr
                ON cr.game_db_id = g.id AND cr.fulfilled_at IS NULL
              LEFT JOIN inv_agg ia
                ON ia.state_code = g.state_code AND ia.game_name_lc = LOWER(g.name)
             WHERE g.is_active = TRUE
               AND ($2::text IS NULL OR g.state_code = $2)
             GROUP BY g.id, ia.in_count, ia.out_count
            HAVING COUNT(cr.id) > 0 OR g.return_pct IS NOT NULL
             ORDER BY vote_count DESC, g.return_pct DESC NULLS LAST
             LIMIT $3
        """, user["uid"], state_code, limit)
    return {
        "items": [
            {
                "game_db_id": r["game_db_id"],
                "state_code": r["state_code"],
                "name": r["name"],
                "price": r["price"],
                "return_pct": r["return_pct"],
                "vote_count": int(r["vote_count"] or 0),
                "user_voted": bool(r["user_voted"]),
                "last_voted_at": r["last_voted_at"].isoformat() if r["last_voted_at"] else None,
                "in_count": int(r["in_count"] or 0),
                "out_count": int(r["out_count"] or 0),
            }
            for r in rows
        ],
    }


@router.get("/my-votes")
async def my_votes(user: dict = Depends(require_member)) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT cr.game_db_id, cr.created_at, cr.fulfilled_at,
                      g.state_code, g.name
                 FROM chase_requests cr
                 JOIN games g ON g.id = cr.game_db_id
                WHERE cr.user_id = $1
                ORDER BY cr.created_at DESC""",
            user["uid"],
        )
    return {
        "items": [
            {
                "game_db_id": r["game_db_id"],
                "state_code": r["state_code"],
                "name": r["name"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "fulfilled_at": r["fulfilled_at"].isoformat() if r["fulfilled_at"] else None,
            }
            for r in rows
        ],
    }


@router.get("/queue")
async def admin_queue(limit: int = 50, user: dict = Depends(require_admin)) -> dict[str, Any]:
    """Caller routing list — top pending tickets by vote count, joined with
    the most recent inventory observation per game so it's obvious which
    requests are stale and worth dialing next."""
    limit = max(1, min(limit, 200))
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT g.id                AS game_db_id,
                   g.state_code,
                   g.name,
                   g.price,
                   g.return_pct,
                   COUNT(cr.id)        AS vote_count,
                   MAX(cr.created_at)  AS last_voted_at,
                   (SELECT MAX(ir.reported_at)
                      FROM inventory_reports ir
                     WHERE ir.state_code = g.state_code
                       AND ir.game_name = g.name) AS last_inventory_at
              FROM games g
              JOIN chase_requests cr
                ON cr.game_db_id = g.id AND cr.fulfilled_at IS NULL
             GROUP BY g.id
             ORDER BY vote_count DESC, last_voted_at DESC
             LIMIT $1
        """, limit)
    return {
        "items": [
            {
                "game_db_id": r["game_db_id"],
                "state_code": r["state_code"],
                "name": r["name"],
                "price": r["price"],
                "return_pct": r["return_pct"],
                "vote_count": int(r["vote_count"] or 0),
                "last_voted_at": r["last_voted_at"].isoformat() if r["last_voted_at"] else None,
                "last_inventory_at": r["last_inventory_at"].isoformat() if r["last_inventory_at"] else None,
            }
            for r in rows
        ],
    }


class UpgradeIntentBody(BaseModel):
    source: str | None = None
    state_code: str | None = None


@router.post("/upgrade-intent")
async def upgrade_intent(body: UpgradeIntentBody) -> dict[str, Any]:
    """Free / logged-out click on the locked request CTA. Fires a PostHog
    conversion event so we can size the funnel; no DB row is written."""
    analytics.capture("anon", "chase_upgrade_intent", {
        "source": body.source or "unknown",
        "state_code": body.state_code,
    })
    return {"ok": True}
