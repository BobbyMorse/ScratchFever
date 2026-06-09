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

def _profile_to_dict(p) -> dict:
    return {
        "id": p["id"],
        "retailer_id": p["retailer_id"],
        "state_code": p["state_code"],
        "store_name": p["store_name"],
        "city": p["city"],
        "zip": p["zip"],
        "phone": p["phone"],
        "verified": p["verified"],
        "created_at": p["created_at"].isoformat(),
        "description": p["description"],
        "website": p["website"],
        "contact_email": p["contact_email"],
        "hours_text": p["hours_text"],
        "photo_url": p["photo_url"],
        "banner_text": p["banner_text"],
        "banner_until": p["banner_until"].isoformat() if p["banner_until"] else None,
    }


@router.get("/me")
async def get_retailer_me(user: dict = Depends(require_retailer)):
    async with get_pool().acquire() as conn:
        profile = await conn.fetchrow(
            """SELECT id, user_id, retailer_id, state_code, store_name,
                      city, zip, phone, verified, created_at,
                      description, website, contact_email, hours_text,
                      photo_url, banner_text, banner_until
               FROM retailer_profiles WHERE user_id=$1""",
            user["uid"],
        )
    if not profile:
        raise HTTPException(status_code=404, detail="No retailer profile linked to this account. Submit a store claim from the store's page.")
    return _profile_to_dict(profile)


class ProfileEditBody(BaseModel):
    store_name: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None
    contact_email: Optional[str] = None
    hours_text: Optional[str] = None
    photo_url: Optional[str] = None
    banner_text: Optional[str] = None
    banner_until: Optional[datetime.datetime] = None


@router.put("/me")
async def update_retailer_me(body: ProfileEditBody, user: dict = Depends(require_retailer)):
    # Sanitize text fields up front; we trust nothing from the client.
    def _clip(val: Optional[str], maxlen: int) -> Optional[str]:
        if val is None:
            return None
        v = val.strip()
        return v[:maxlen] if v else None

    store_name    = _clip(body.store_name, 120)
    phone         = _clip(body.phone, 30)
    description   = _clip(body.description, 1000)
    website       = _clip(body.website, 300)
    contact_email = _clip(body.contact_email, 200)
    hours_text    = _clip(body.hours_text, 500)
    photo_url     = _clip(body.photo_url, 500)
    banner_text   = _clip(body.banner_text, 200)
    banner_until  = body.banner_until

    if website and not (website.startswith("http://") or website.startswith("https://")):
        website = "https://" + website

    async with get_pool().acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, store_name FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="No retailer profile found")
        # Use COALESCE so omitted fields stay unchanged; explicit "" from the
        # client (sanitized to None above) is treated as "no change", not clear.
        # Owners can clear a field by sending the literal string "—" or null —
        # for now we leave clearing out of scope; banner_until is the one
        # explicit nullable knob (set/expire the announce banner).
        await conn.execute(
            """UPDATE retailer_profiles SET
                 store_name    = COALESCE($2, store_name),
                 phone         = COALESCE($3, phone),
                 description   = COALESCE($4, description),
                 website       = COALESCE($5, website),
                 contact_email = COALESCE($6, contact_email),
                 hours_text    = COALESCE($7, hours_text),
                 photo_url     = COALESCE($8, photo_url),
                 banner_text   = $9,
                 banner_until  = $10,
                 updated_at    = NOW()
               WHERE user_id=$1""",
            user["uid"], store_name, phone, description, website, contact_email,
            hours_text, photo_url, banner_text, banner_until,
        )
        profile = await conn.fetchrow(
            """SELECT id, user_id, retailer_id, state_code, store_name,
                      city, zip, phone, verified, created_at,
                      description, website, contact_email, hours_text,
                      photo_url, banner_text, banner_until
               FROM retailer_profiles WHERE user_id=$1""",
            user["uid"],
        )
    return _profile_to_dict(profile)


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


@public_router.get("/feed/promotions")
async def public_promo_feed(
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lng: Optional[float] = Query(None, ge=-180, le=180),
    radius_mi: float = Query(25.0, gt=0, le=500),
    limit: int = Query(20, ge=1, le=100),
    days: int = Query(14, ge=1, le=90),
):
    """Recent promotions from claimed stores, optionally filtered by user
    location. Drives the homepage 'Live store updates near you' card —
    the demand-side push that makes store-owner promotions actually visible.

    Distance is computed in Postgres using the Haversine formula when both
    user coords and the store's lat/lng are present. Stores without lat/lng
    still appear (with distance_mi = NULL) when no location filter is set.
    """
    # MA is currently the only state where claimed stores live in a table
    # we can join for lat/lng (ma_retailers). Other states only show without
    # distance for now; expanding here is a 1:1 UNION ALL per new state table.
    use_location = lat is not None and lng is not None
    args: list = [days]
    distance_sql = "NULL::float AS distance_mi"
    where_extra = ""
    order_sql = "p.created_at DESC"
    if use_location:
        # 3958.8 = Earth radius in miles
        distance_sql = (
            "CASE WHEN ma.latitude IS NULL OR ma.longitude IS NULL THEN NULL "
            "ELSE 3958.8 * acos(LEAST(1.0, "
            "  sin(radians($2)) * sin(radians(ma.latitude)) "
            "  + cos(radians($2)) * cos(radians(ma.latitude)) "
            "  * cos(radians(ma.longitude) - radians($3))"
            ")) END AS distance_mi"
        )
        args.extend([lat, lng])
        # Don't drop stores we can't geo-locate; include them after the radius hits.
        where_extra = (
            f" AND (ma.latitude IS NULL OR "
            f"  (3958.8 * acos(LEAST(1.0, "
            f"    sin(radians(${len(args)-1})) * sin(radians(ma.latitude)) "
            f"    + cos(radians(${len(args)-1})) * cos(radians(ma.latitude)) "
            f"    * cos(radians(ma.longitude) - radians(${len(args)}))"
            f"  ))) <= ${len(args)+1})"
        )
        args.append(radius_mi)
        order_sql = "distance_mi NULLS LAST, p.created_at DESC"
    args.append(limit)

    sql = f"""
        SELECT p.id, p.retailer_id, p.store_name, p.title, p.body, p.created_at,
               rp.state_code, rp.city, rp.verified,
               ma.latitude, ma.longitude,
               {distance_sql}
        FROM retailer_posts p
        LEFT JOIN retailer_profiles rp ON rp.retailer_id = p.retailer_id
        LEFT JOIN ma_retailers ma ON rp.state_code = 'MA'
                                   AND ma.id::text = p.retailer_id
        WHERE p.created_at > NOW() - INTERVAL '1 day' * $1
          {where_extra}
        ORDER BY {order_sql}
        LIMIT ${len(args)}
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return {
        "promotions": [
            {
                "id": r["id"],
                "retailer_id": r["retailer_id"],
                "store_name": r["store_name"] or "Lottery Retailer",
                "city": r["city"],
                "state_code": r["state_code"],
                "verified": r["verified"],
                "title": r["title"],
                "body": r["body"],
                "created_at": r["created_at"].isoformat(),
                "lat": r["latitude"],
                "lng": r["longitude"],
                "distance_mi": float(r["distance_mi"]) if r["distance_mi"] is not None else None,
            }
            for r in rows
        ]
    }


# Whitelist for the summary endpoint's dynamic table lookup. Keeping this
# inline (vs. importing from backend.database) so the route fails closed if a
# state-code spoof slips past validation — the f-string below ONLY interpolates
# from this dict's values.
_PER_STATE_RETAILER_TABLES_PUBLIC = {
    "MA": "ma_retailers",
    "AZ": "az_retailers",
    "FL": "fl_retailers",
    "GA": "ga_retailers",
    "RI": "ri_retailers",
}


@public_router.get("/retailer/{state_code}/{retailer_id}/summary")
async def get_public_retailer_summary(state_code: str, retailer_id: str):
    """Everything the public /store/{id} page needs to render an unclaimed
    store usefully: address + lat/lng (from the per-state retailer table or
    state_retailers), whether the store is claimed, and the last-14d community
    inventory status grouped by game.

    The page calls this only when /profile returns null OR when it wants to
    show address + map alongside the owner content. Always-on, no auth — the
    same data already shows up on the homepage map for everyone."""
    code = (state_code or "").strip().upper()
    if len(code) != 2:
        raise HTTPException(status_code=400, detail="state_code must be a 2-letter code")
    rid = (retailer_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="retailer_id required")

    async with get_pool().acquire() as conn:
        table = _PER_STATE_RETAILER_TABLES_PUBLIC.get(code)
        if table:
            # Per-state tables key on the textual store id (e.g. MA agent #).
            row = await conn.fetchrow(
                f"""SELECT id, name, address, city, zip_code, phone,
                           latitude, longitude
                    FROM {table} WHERE id=$1""",
                rid,
            )
        else:
            # Generic state_retailers — id is SERIAL int; the frontend
            # stringifies it before passing it back in URLs.
            try:
                rid_int = int(rid)
            except ValueError:
                rid_int = -1
            row = await conn.fetchrow(
                """SELECT id, name, address, city, zip_code, phone,
                          latitude, longitude
                   FROM state_retailers
                   WHERE id=$1 AND state_code=$2""",
                rid_int, code,
            )
        if not row:
            raise HTTPException(status_code=404, detail="Retailer not found")

        has_profile = bool(await conn.fetchval(
            "SELECT 1 FROM retailer_profiles WHERE retailer_id=$1 AND state_code=$2",
            rid, code,
        ))

        # Last-14d community + retailer + caller reports, latest status per game.
        # DISTINCT ON keeps only the most recent row per lowercased game name.
        inv_rows = await conn.fetch(
            """SELECT DISTINCT ON (LOWER(game_name))
                   game_name, game_price, has_stock, source, reported_at
               FROM inventory_reports
               WHERE retailer_id=$1
                 AND reported_at > NOW() - INTERVAL '14 days'
               ORDER BY LOWER(game_name), reported_at DESC""",
            rid,
        )

    return {
        "retailer": {
            "id":         str(row["id"]),
            "state_code": code,
            "name":       row["name"] or "",
            "address":    row["address"] or "",
            "city":       row["city"] or "",
            "zip_code":   row["zip_code"] or "",
            "phone":      row["phone"] or "",
            "latitude":   row["latitude"],
            "longitude":  row["longitude"],
        },
        "has_profile": has_profile,
        "inventory": [
            {
                "game_name":   r["game_name"],
                "game_price":  r["game_price"],
                "has_stock":   r["has_stock"],
                "source":      r["source"],
                "reported_at": r["reported_at"].isoformat(),
            }
            for r in inv_rows
        ],
    }


@public_router.get("/retailer/{retailer_id}/profile")
async def get_public_retailer_profile(retailer_id: str):
    """Owner-published store profile fields, surfaced on the consumer-side
    retailer card. Only returns rows whose claim was approved (verified=TRUE
    or any retailer_profiles row, which only exists post-approval)."""
    async with get_pool().acquire() as conn:
        p = await conn.fetchrow(
            """SELECT store_name, city, state_code, phone, verified,
                      description, website, contact_email, hours_text,
                      photo_url, banner_text, banner_until
               FROM retailer_profiles WHERE retailer_id=$1
               ORDER BY id LIMIT 1""",
            retailer_id,
        )
    if not p:
        return {"profile": None}
    banner_active = (
        bool(p["banner_text"])
        and (p["banner_until"] is None or p["banner_until"] > datetime.datetime.now(datetime.timezone.utc))
    )
    return {
        "profile": {
            "store_name":    p["store_name"],
            "city":          p["city"],
            "state_code":    p["state_code"],
            "phone":         p["phone"],
            "verified":      p["verified"],
            "description":   p["description"],
            "website":       p["website"],
            "contact_email": p["contact_email"],
            "hours_text":    p["hours_text"],
            "photo_url":     p["photo_url"],
            "banner_text":   p["banner_text"] if banner_active else None,
        }
    }


# ── Claim flow ────────────────────────────────────────────────────────────────

class ClaimBody(BaseModel):
    retailer_id: str
    state_code: str
    store_name: str
    city: Optional[str] = None
    zip: Optional[str] = None
    phone: Optional[str] = None
    claimant_role: Optional[str] = None   # owner | manager | clerk | other
    claimant_name: Optional[str] = None
    claimant_phone: Optional[str] = None
    notes: Optional[str] = None


@router.post("/claim")
async def submit_claim(body: ClaimBody, user: dict = Depends(require_member)):
    """Any logged-in member can submit a claim for a specific retailer.
    Blocks duplicates (same user, same retailer, still pending) and blocks
    users who already own an approved store."""
    rid = (body.retailer_id or "").strip()
    state_code = (body.state_code or "").strip().upper()
    store_name = (body.store_name or "").strip()
    if not rid or not state_code or not store_name:
        raise HTTPException(status_code=400, detail="retailer_id, state_code, and store_name are required")
    if len(state_code) != 2:
        raise HTTPException(status_code=400, detail="state_code must be a 2-letter code")

    async with get_pool().acquire() as conn:
        existing_profile = await conn.fetchval(
            "SELECT id FROM retailer_profiles WHERE user_id=$1", user["uid"]
        )
        if existing_profile:
            raise HTTPException(status_code=409, detail="This account already owns a store. Contact support to claim a second location.")
        pending_dup = await conn.fetchval(
            """SELECT id FROM retailer_claims
               WHERE user_id=$1 AND retailer_id=$2 AND state_code=$3 AND status='pending'""",
            user["uid"], rid, state_code,
        )
        if pending_dup:
            raise HTTPException(status_code=409, detail="You already have a pending claim for this store.")
        row = await conn.fetchrow(
            """INSERT INTO retailer_claims
               (user_id, retailer_id, state_code, store_name, city, zip, phone,
                claimant_role, claimant_name, claimant_phone, notes)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
               RETURNING id, created_at""",
            user["uid"], rid, state_code, store_name[:120],
            (body.city or "").strip()[:120] or None,
            (body.zip or "").strip()[:20] or None,
            (body.phone or "").strip()[:30] or None,
            (body.claimant_role or "").strip()[:30] or None,
            (body.claimant_name or "").strip()[:120] or None,
            (body.claimant_phone or "").strip()[:30] or None,
            (body.notes or "").strip()[:1000] or None,
        )
    return {"id": row["id"], "status": "pending", "created_at": row["created_at"].isoformat()}


@router.get("/my-claims")
async def list_my_claims(user: dict = Depends(require_member)):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, retailer_id, state_code, store_name, city, status,
                      created_at, reviewed_at, review_notes
               FROM retailer_claims WHERE user_id=$1
               ORDER BY created_at DESC""",
            user["uid"],
        )
    return {
        "claims": [
            {
                "id": r["id"],
                "retailer_id": r["retailer_id"],
                "state_code": r["state_code"],
                "store_name": r["store_name"],
                "city": r["city"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat(),
                "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
                "review_notes": r["review_notes"],
            }
            for r in rows
        ]
    }


# ── Admin: claim review ───────────────────────────────────────────────────────

@admin_router.get("/claims")
async def admin_list_claims(
    status: Optional[str] = Query(None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(100, le=500),
    admin: dict = Depends(require_admin),
):
    where = "WHERE c.status=$1" if status else ""
    args = [status] if status else []
    args.append(limit)
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT c.id, c.user_id, c.retailer_id, c.state_code, c.store_name,
                       c.city, c.zip, c.phone, c.claimant_role, c.claimant_name,
                       c.claimant_phone, c.notes, c.status, c.created_at,
                       c.reviewed_at, c.review_notes,
                       u.email AS user_email, u.username AS user_username
                FROM retailer_claims c
                JOIN users u ON u.id = c.user_id
                {where}
                ORDER BY c.created_at DESC
                LIMIT ${len(args)}""",
            *args,
        )
    return {
        "claims": [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "user_email": r["user_email"],
                "user_username": r["user_username"],
                "retailer_id": r["retailer_id"],
                "state_code": r["state_code"],
                "store_name": r["store_name"],
                "city": r["city"],
                "zip": r["zip"],
                "phone": r["phone"],
                "claimant_role": r["claimant_role"],
                "claimant_name": r["claimant_name"],
                "claimant_phone": r["claimant_phone"],
                "notes": r["notes"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat(),
                "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
                "review_notes": r["review_notes"],
            }
            for r in rows
        ]
    }


class ClaimReviewBody(BaseModel):
    review_notes: Optional[str] = None


@admin_router.post("/claims/{claim_id}/approve")
async def admin_approve_claim(claim_id: int, body: ClaimReviewBody, admin: dict = Depends(require_admin)):
    """Approve a claim. Idempotent on the role bump; the profile insert is
    rejected (409) if the user already owns a store."""
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            c = await conn.fetchrow(
                "SELECT id, user_id, retailer_id, state_code, store_name, city, zip, phone, status FROM retailer_claims WHERE id=$1 FOR UPDATE",
                claim_id,
            )
            if not c:
                raise HTTPException(status_code=404, detail="Claim not found")
            if c["status"] != "pending":
                raise HTTPException(status_code=409, detail=f"Claim is already {c['status']}")
            already_owns = await conn.fetchval(
                "SELECT id FROM retailer_profiles WHERE user_id=$1", c["user_id"]
            )
            if already_owns:
                raise HTTPException(status_code=409, detail="User already owns a store. Reject this claim or remove their existing profile first.")
            await conn.execute(
                """INSERT INTO retailer_profiles
                   (user_id, retailer_id, state_code, store_name, city, zip, phone, verified)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE)""",
                c["user_id"], c["retailer_id"], c["state_code"], c["store_name"],
                c["city"], c["zip"], c["phone"],
            )
            await conn.execute(
                "UPDATE users SET role='retailer' WHERE id=$1 AND role='member'",
                c["user_id"],
            )
            await conn.execute(
                """UPDATE retailer_claims
                   SET status='approved', reviewed_at=NOW(),
                       reviewed_by=$2, review_notes=$3
                   WHERE id=$1""",
                claim_id, admin["uid"], (body.review_notes or "").strip()[:1000] or None,
            )
    return {"id": claim_id, "status": "approved"}


@admin_router.post("/claims/{claim_id}/reject")
async def admin_reject_claim(claim_id: int, body: ClaimReviewBody, admin: dict = Depends(require_admin)):
    async with get_pool().acquire() as conn:
        c = await conn.fetchrow(
            "SELECT status FROM retailer_claims WHERE id=$1", claim_id
        )
        if not c:
            raise HTTPException(status_code=404, detail="Claim not found")
        if c["status"] != "pending":
            raise HTTPException(status_code=409, detail=f"Claim is already {c['status']}")
        await conn.execute(
            """UPDATE retailer_claims
               SET status='rejected', reviewed_at=NOW(),
                   reviewed_by=$2, review_notes=$3
               WHERE id=$1""",
            claim_id, admin["uid"], (body.review_notes or "").strip()[:1000] or None,
        )
    return {"id": claim_id, "status": "rejected"}
