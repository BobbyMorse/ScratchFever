"""
Retailer portal API — authenticated routes for store operators.
"""
from __future__ import annotations
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.database import get_pool
from backend.users import require_member, require_admin

router = APIRouter(prefix="/api/retailer", tags=["retailer"])
public_router = APIRouter(prefix="/api/public", tags=["public"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin-retailer"])


async def require_retailer(user: dict = Depends(require_member)) -> dict:
    """
    Pass if the token role is retailer/admin, OR (fallback) the user already
    has a retailer_profiles row. The DB fallback exists because tokens live
    30 days and aren't re-issued when admin approves a claim — without it,
    a freshly approved user would be locked out until their next login.
    """
    if user.get("role") in ("retailer", "admin"):
        return user
    async with get_pool().acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
    if not row:
        raise HTTPException(status_code=403, detail="Retailer access required")
    return user


# ── Games (all active, no EV filter) ──────────────────────────────────────────

@router.get("/games")
async def retailer_games(user: dict = Depends(require_retailer)):
    """All active games for the retailer's state — no ev IS NOT NULL filter."""
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT state_code FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if not profile:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        rows = await conn.fetch(
            """SELECT id, game_id, name, price, ev, return_pct,
                      overall_odds_one_in, top_prize, top_prize_remaining,
                      detail_url, image_url
               FROM games
               WHERE state_code=$1 AND is_active=TRUE
               ORDER BY name""",
            profile["state_code"],
        )
    return {
        "games": [
            {
                "id": r["id"], "game_id": r["game_id"], "name": r["name"],
                "price": r["price"], "ev": r["ev"], "return_pct": r["return_pct"],
                "overall_odds_one_in": r["overall_odds_one_in"],
                "top_prize": r["top_prize"], "top_prize_remaining": r["top_prize_remaining"],
                "detail_url": r["detail_url"], "image_url": r["image_url"],
            }
            for r in rows
        ]
    }


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/me")
async def get_retailer_me(user: dict = Depends(require_retailer)):
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            """SELECT id, user_id, retailer_id, state_code, store_name,
                      city, zip, phone, verified, created_at
               FROM retailer_profiles WHERE user_id=$1""",
            user["uid"],
        )
    if not profile:
        raise HTTPException(status_code=404, detail="No retailer profile linked to this account. Contact support.")
    return {
        "id": profile["id"],
        "retailer_id": profile["retailer_id"],
        "state_code": profile["state_code"],
        "store_name": profile["store_name"],
        "city": profile["city"],
        "zip": profile["zip"],
        "phone": profile["phone"],
        "verified": profile["verified"],
        "created_at": profile["created_at"].isoformat(),
    }


# ── Inventory ─────────────────────────────────────────────────────────────────

class InventoryItem(BaseModel):
    game_name: str
    game_price: Optional[float] = None
    has_stock: bool
    notes: Optional[str] = None


class BulkInventoryBody(BaseModel):
    items: list[InventoryItem]


@router.post("/inventory")
async def retailer_update_inventory(body: BulkInventoryBody, user: dict = Depends(require_retailer)):
    if not body.items:
        raise HTTPException(status_code=400, detail="No items provided")
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT retailer_id, store_name, city FROM retailer_profiles WHERE user_id=$1",
            user["uid"],
        )
        if not profile:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        now = datetime.datetime.utcnow()
        for item in body.items:
            await conn.execute(
                """INSERT INTO inventory_reports
                   (retailer_id, retailer_name, retailer_city, game_name, game_price,
                    has_stock, source, reporter_username, notes, reported_at)
                   VALUES ($1,$2,$3,$4,$5,$6,'retailer',$7,$8,$9)""",
                profile["retailer_id"], profile["store_name"], profile["city"],
                item.game_name.strip(), item.game_price, item.has_stock,
                user.get("username") or user["email"],
                (item.notes or "").strip() or None, now,
            )
    return {"message": "Inventory updated", "count": len(body.items)}


@router.get("/inventory/latest")
async def get_retailer_inventory_latest(user: dict = Depends(require_retailer)):
    """Latest in-stock status per game (most recent retailer report per game name)."""
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT retailer_id FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if not profile:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        rows = await conn.fetch(
            """SELECT DISTINCT ON (LOWER(game_name))
                   game_name, has_stock, notes, reported_at
               FROM inventory_reports
               WHERE retailer_id=$1 AND source='retailer'
               ORDER BY LOWER(game_name), reported_at DESC""",
            profile["retailer_id"],
        )
    return {
        "status": {
            r["game_name"].lower(): {
                "has_stock": r["has_stock"],
                "notes": r["notes"],
                "reported_at": r["reported_at"].isoformat(),
            }
            for r in rows
        }
    }


@router.get("/inventory")
async def get_retailer_inventory_history(
    limit: int = Query(100, le=500),
    user: dict = Depends(require_retailer),
):
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT retailer_id FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if not profile:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        rows = await conn.fetch(
            """SELECT id, game_name, game_price, has_stock, notes, reported_at
               FROM inventory_reports
               WHERE retailer_id=$1 AND source='retailer'
               ORDER BY reported_at DESC LIMIT $2""",
            profile["retailer_id"], limit,
        )
    return {
        "reports": [
            {
                "id": r["id"], "game_name": r["game_name"], "game_price": r["game_price"],
                "has_stock": r["has_stock"], "notes": r["notes"],
                "reported_at": r["reported_at"].isoformat(),
            }
            for r in rows
        ]
    }


# ── Posts ─────────────────────────────────────────────────────────────────────

class PostBody(BaseModel):
    title: str
    body: Optional[str] = None


@router.post("/posts")
async def create_retailer_post(post: PostBody, user: dict = Depends(require_retailer)):
    title = (post.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title required")
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT retailer_id, store_name FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if not profile:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        row = await conn.fetchrow(
            """INSERT INTO retailer_posts (retailer_id, store_name, title, body)
               VALUES ($1,$2,$3,$4) RETURNING id, created_at""",
            profile["retailer_id"], profile["store_name"],
            title[:200], ((post.body or "").strip()[:2000] or None),
        )
    return {"id": row["id"], "created_at": row["created_at"].isoformat()}


@router.get("/posts")
async def get_retailer_posts(user: dict = Depends(require_retailer)):
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT retailer_id FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if not profile:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        rows = await conn.fetch(
            """SELECT id, title, body, created_at FROM retailer_posts
               WHERE retailer_id=$1 ORDER BY created_at DESC LIMIT 50""",
            profile["retailer_id"],
        )
    return {
        "posts": [
            {"id": r["id"], "title": r["title"], "body": r["body"],
             "created_at": r["created_at"].isoformat()}
            for r in rows
        ]
    }


@router.delete("/posts/{post_id}")
async def delete_retailer_post(post_id: int, user: dict = Depends(require_retailer)):
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT retailer_id FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if not profile:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        result = await conn.execute(
            "DELETE FROM retailer_posts WHERE id=$1 AND retailer_id=$2",
            post_id, profile["retailer_id"],
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Post not found")
    return {"message": "Deleted"}


# ── Activity stats ────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_retailer_stats(user: dict = Depends(require_retailer)):
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT retailer_id FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if not profile:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        rid = profile["retailer_id"]
        comm_total = await conn.fetchval(
            "SELECT COUNT(*) FROM inventory_reports WHERE retailer_id=$1 AND source='community'", rid
        )
        comm_7d = await conn.fetchval(
            """SELECT COUNT(*) FROM inventory_reports
               WHERE retailer_id=$1 AND source='community'
               AND reported_at > NOW() - INTERVAL '7 days'""", rid
        )
        own_total = await conn.fetchval(
            "SELECT COUNT(*) FROM inventory_reports WHERE retailer_id=$1 AND source='retailer'", rid
        )
        post_count = await conn.fetchval(
            "SELECT COUNT(*) FROM retailer_posts WHERE retailer_id=$1", rid
        )
        recent = await conn.fetch(
            """SELECT game_name, has_stock, reporter_username, reported_at
               FROM inventory_reports
               WHERE retailer_id=$1 AND source='community'
               ORDER BY reported_at DESC LIMIT 10""",
            rid,
        )
    return {
        "community_reports_total": comm_total or 0,
        "community_reports_7d": comm_7d or 0,
        "own_updates_total": own_total or 0,
        "post_count": post_count or 0,
        "recent_community": [
            {
                "game_name": r["game_name"],
                "has_stock": r["has_stock"],
                "reporter": r["reporter_username"],
                "reported_at": r["reported_at"].isoformat(),
            }
            for r in recent
        ],
    }


# ── Public routes (no auth) ───────────────────────────────────────────────────

@public_router.get("/retailer/{retailer_id}/posts")
async def get_public_retailer_posts(retailer_id: str, limit: int = Query(10, le=50)):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, store_name, title, body, created_at
               FROM retailer_posts WHERE retailer_id=$1
               ORDER BY created_at DESC LIMIT $2""",
            retailer_id, limit,
        )
    return {
        "posts": [
            {
                "id": r["id"], "store_name": r["store_name"],
                "title": r["title"], "body": r["body"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
    }
