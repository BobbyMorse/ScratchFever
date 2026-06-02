"""
REST API for managing call campaigns, viewing results, and controlling the runner.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth import require_admin
from backend.caller.db import (
    create_campaign, get_campaign, get_hits, get_queue, get_queue_stats,
    get_recent_results, list_campaigns, populate_queue, update_campaign_status, mark_dnc,
    mark_no_inventory, mark_queue_no_inventory, add_manual_check, get_manual_checks,
)

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])

# Injected by main.py after runner is created
_runner = None


def set_runner(runner):
    global _runner
    _runner = runner


def get_runner():
    return _runner


# ── Request models ────────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    game_name: str
    game_number: str = ""
    game_price: float = 0.0
    target_tiers: str = "ELITE,GOOD"
    max_stores: int = 0   # 0 = no limit (call all matching stores)
    call_backend: str = "twilio_ivr"  # "twilio_ivr" or "twilio"


class TestCallBody(BaseModel):
    phone: str
    game_name: str = "Test Game"
    game_number: str = ""
    game_price: float = 10.0


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/api/caller/campaigns")
async def create_campaign_endpoint(body: CampaignCreate):
    try:
        tiers = [t.strip().upper() for t in body.target_tiers.split(",")]
        max_stores = body.max_stores if body.max_stores > 0 else 9999
        campaign_id = await create_campaign(
            game_name=body.game_name,
            game_number=body.game_number,
            game_price=body.game_price,
            target_tiers=",".join(tiers),
            max_stores=max_stores,
            call_backend=body.call_backend,
        )
        inserted = await populate_queue(campaign_id, tiers, max_stores)
        campaign = await get_campaign(campaign_id)
        return {
            "campaign": campaign,
            "queue_loaded": inserted,
            "message": f"Campaign #{campaign_id} created with {inserted} stores queued.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error creating campaign")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/caller/campaigns")
async def list_campaigns_endpoint():
    campaigns = await list_campaigns()
    return {"campaigns": campaigns, "count": len(campaigns)}


@router.get("/api/caller/campaigns/{campaign_id}")
async def get_campaign_endpoint(campaign_id: int):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    stats = await get_queue_stats(campaign_id)
    recent = await get_recent_results(campaign_id, limit=20)
    hits = await get_hits(campaign_id)
    return {
        "campaign": campaign,
        "queue_stats": stats,
        "recent_results": recent,
        "hits": hits,
    }


@router.post("/api/caller/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: int):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if _runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialized")
    await update_campaign_status(campaign_id, "active")
    _runner.start_campaign(campaign_id)
    return {"message": f"Campaign #{campaign_id} started", "campaign_id": campaign_id}


@router.post("/api/caller/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if _runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialized")
    _runner.stop_campaign(campaign_id)
    await update_campaign_status(campaign_id, "paused")
    return {"message": f"Campaign #{campaign_id} paused", "campaign_id": campaign_id}


@router.delete("/api/caller/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if _runner is not None:
        _runner.stop_campaign(campaign_id)
    from backend.database import get_pool
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM call_results WHERE campaign_id=$1", campaign_id)
        await conn.execute("DELETE FROM call_queue WHERE campaign_id=$1", campaign_id)
        await conn.execute("DELETE FROM call_campaigns WHERE id=$1", campaign_id)
    return {"message": f"Campaign #{campaign_id} deleted"}


@router.post("/api/caller/campaigns/{campaign_id}/expand")
async def expand_campaign_queue(campaign_id: int):
    """Add ALL stores matching this campaign's tiers into the queue (no cap)."""
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    tiers = [t.strip().upper() for t in (campaign["target_tiers"] or "ELITE,GOOD").split(",")]
    added = await populate_queue(campaign_id, tiers, max_stores=9999)
    return {"message": f"{added} new stores added to queue.", "added": added}


@router.get("/api/caller/campaigns/{campaign_id}/queue")
async def get_campaign_queue(
    campaign_id: int,
    status: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=9999),
    offset: int = Query(0, ge=0),
):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    queue = await get_queue(campaign_id, status=status, limit=limit, offset=offset)
    return {"queue": queue, "count": len(queue)}


@router.post("/api/caller/results/{result_id}/no_inventory")
async def mark_result_no_inventory(result_id: int):
    found = await mark_no_inventory(result_id)
    if not found:
        raise HTTPException(status_code=404, detail="Result not found")
    return {"message": f"Result #{result_id} marked as no inventory"}


@router.post("/api/caller/queue/{queue_id}/no_inventory")
async def mark_queue_entry_no_inventory(queue_id: int):
    found = await mark_queue_no_inventory(queue_id)
    if not found:
        raise HTTPException(status_code=404, detail="Queue entry not found or not in pending state")
    return {"message": f"Store marked as no inventory"}


class ManualCheckBody(BaseModel):
    retailer_id: str
    name: str = ""
    address: str = ""
    city: str = ""
    phone: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    has_inventory: int = 0
    notes: str = ""


@router.post("/api/caller/campaigns/{campaign_id}/manual_check")
async def record_manual_check(campaign_id: int, body: ManualCheckBody):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await add_manual_check(
        campaign_id, body.retailer_id, body.name, body.address,
        body.city, body.phone, body.lat, body.lng, body.has_inventory, body.notes,
    )
    return {"message": "Manual check recorded"}


@router.get("/api/caller/campaigns/{campaign_id}/retailers")
async def get_campaign_all_retailers(campaign_id: int):
    """All MA retailers with their status for this campaign (for map + full list)."""
    import asyncio
    from backend.ma_scorer import load_and_score
    from backend.database import get_pool

    retailers = await asyncio.to_thread(load_and_score)

    async with get_pool().acquire() as conn:
        queue_rows = await conn.fetch(
            "SELECT retailer_id, id, status, attempts FROM call_queue WHERE campaign_id=$1",
            campaign_id,
        )
        queue_map = {r["retailer_id"]: dict(r) for r in queue_rows}

        result_rows = await conn.fetch(
            """SELECT cq.retailer_id, cr.has_game, cr.called_at
               FROM call_results cr JOIN call_queue cq ON cq.id = cr.queue_id
               WHERE cr.campaign_id=$1 ORDER BY cr.called_at DESC""",
            campaign_id,
        )
        result_map = {}
        for r in result_rows:
            if r["retailer_id"] not in result_map:
                result_map[r["retailer_id"]] = {"has_game": r["has_game"], "called_at": r["called_at"]}

        manual_rows = await conn.fetch(
            "SELECT retailer_id, has_inventory, notes, checked_at FROM manual_checks WHERE campaign_id=$1",
            campaign_id,
        )
        manual_map = {r["retailer_id"]: dict(r) for r in manual_rows}

    merged = []
    for r in retailers:
        rid = r["id"]
        if rid in result_map:
            cstatus = "hit" if result_map[rid]["has_game"] else "no_stock"
            has_inv = result_map[rid]["has_game"]
            checked_at = result_map[rid]["called_at"]
            queue_id = queue_map.get(rid, {}).get("id")
        elif rid in manual_map:
            cstatus = "hit" if manual_map[rid]["has_inventory"] else "no_stock"
            has_inv = manual_map[rid]["has_inventory"]
            checked_at = manual_map[rid].get("checked_at")
            queue_id = queue_map.get(rid, {}).get("id")
        elif rid in queue_map:
            cstatus = queue_map[rid]["status"]
            has_inv = None
            checked_at = None
            queue_id = queue_map[rid]["id"]
        else:
            cstatus = "unchecked"
            has_inv = None
            checked_at = None
            queue_id = None

        merged.append({
            "id": rid,
            "name": r["name"],
            "address": r["address"],
            "city": r["city"],
            "phone": r.get("phone", ""),
            "score": r["score"],
            "tier": r["tier"],
            "lat": r.get("latitude"),
            "lng": r.get("longitude"),
            "campaign_status": cstatus,
            "has_inventory": has_inv,
            "checked_at": checked_at,
            "queue_id": queue_id,
            "attempts": queue_map.get(rid, {}).get("attempts", 0),
        })

    return {"retailers": merged, "total": len(merged)}


@router.post("/api/caller/queue/{queue_id}/dnc")
async def do_not_call(queue_id: int):
    await mark_dnc(queue_id)
    return {"message": f"Store marked do-not-call (queue entry {queue_id})"}


@router.get("/api/caller/hits")
async def get_all_hits(campaign_id: Optional[int] = Query(None)):
    hits = await get_hits(campaign_id)
    return {"hits": hits, "count": len(hits)}


@router.post("/api/caller/test-call")
async def test_call_endpoint(body: TestCallBody):
    import os
    from backend.caller.db import normalize_phone
    from backend.caller.webhook import register_call

    e164 = normalize_phone(body.phone)
    if not e164:
        raise HTTPException(status_code=400, detail=f"Invalid phone number: {body.phone}")

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    base_url    = os.getenv("CALLER_BASE_URL", "").rstrip("/")

    if not all([account_sid, auth_token, from_number]):
        raise HTTPException(
            status_code=503,
            detail="Twilio credentials not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER)",
        )
    if not base_url:
        raise HTTPException(status_code=503, detail="CALLER_BASE_URL not set")

    # queue_id=0 is the sentinel for test calls — IVR endpoints skip DB writes for it
    register_call(0, {
        "id": 0,
        "game_name":   body.game_name,
        "game_number": body.game_number,
        "game_price":  body.game_price,
    }, {})

    twiml_url  = f"{base_url}/caller/ivr/twiml/0"
    status_url = f"{base_url}/caller/ivr/status/0"

    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(account_sid, auth_token)
        call = client.calls.create(
            to=e164,
            from_=from_number,
            url=twiml_url,
            status_callback=status_url,
            status_callback_method="POST",
            timeout=30,
        )
        logger.info("Test IVR call placed to %s SID=%s", e164, call.sid)
        return {"call_sid": call.sid, "to": e164, "status": call.status}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/caller/status")
async def caller_status():
    import os
    bland_key  = os.getenv("BLAND_API_KEY")
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    backend = "bland" if bland_key else ("twilio" if twilio_sid else "not configured")
    if _runner is None:
        return {"runner": "not initialized", "active_campaigns": [], "backend": backend}
    return {
        "runner": "running" if _runner.is_running() else "idle",
        "active_campaigns": list(_runner.active_campaigns),
        "calls_in_flight": _runner.calls_in_flight(),
        "backend": backend,
    }
