"""
Outbound VAPI dispatch — admin-only.

VAPI runs the actual phone calls externally (telephony + STT + LLM + TTS).
This module:
  1. Selects retailers from our DB (state_retailers + state-specific scorers
     for MA/AZ where available)
  2. POSTs to VAPI's REST API to create outbound calls with per-call context
     passed via assistantOverrides.variableValues
  3. Inserts placeholder rows into vapi_calls so the dashboard can show the
     dispatch immediately. The webhook (vapi_webhook.py) later UPSERTs the
     same row with the end-of-call data.

Environment:
  VAPI_PRIVATE_KEY      Bearer token for api.vapi.ai (server-side key)
  VAPI_ASSISTANT_ID     Assistant to run on each call
  VAPI_PHONE_NUMBER_ID  VAPI-managed number to dial from

Routes:
  GET  /api/vapi/config              env-var presence check
  GET  /api/vapi/stats               counts for dashboard cards
  GET  /api/vapi/states              distinct state codes with callable phones
  GET  /api/vapi/retailers           filtered pull from state_retailers
  POST /api/vapi/dispatch            ad-hoc dispatch by explicit retailer_ids
  POST /api/vapi/dispatch_campaign   "campaign-style" dispatch by state+max_stores
  POST /api/vapi/test_call           single test call to an arbitrary number
"""
from __future__ import annotations
import asyncio
import datetime as dt
import itertools
import json
import logging
import os
import re
import threading
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.database import get_pool
from backend.users import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vapi", tags=["vapi"])

VAPI_API_BASE = "https://api.vapi.ai"
MAX_BATCH = 500
CONCURRENCY = 5


def _env_phone_number_ids() -> list[str]:
    """Explicit env-var override, for cases where you want to force a specific
    subset. Empty list → fall back to VAPI account auto-discovery.
      VAPI_PHONE_NUMBER_IDS = "id1,id2,id3"
      VAPI_PHONE_NUMBER_ID  = "id"            ← legacy single, still honored
    """
    multi = os.getenv("VAPI_PHONE_NUMBER_IDS", "")
    ids = [s.strip() for s in multi.split(",") if s.strip()]
    if ids:
        return ids
    single = os.getenv("VAPI_PHONE_NUMBER_ID")
    return [single] if single else []


# ── Auto-discovery + per-day exhaustion tracking ─────────────────────────────
#
# Goal: zero Railway maintenance. Create a phone number in VAPI's dashboard,
# the dispatcher picks it up on the next call. If a number hits VAPI's daily
# cap mid-session, mark it dead for today and use the next one transparently.

_VAPI_NUMBERS_CACHE: Optional[tuple[float, list[str]]] = None
_VAPI_NUMBERS_TTL_SEC = 60  # short — user may create numbers live
_VAPI_NUMBERS_LOCK = asyncio.Lock()

_EXHAUSTED_TODAY: dict[str, str] = {}  # phone_number_id → ISO date stamped
_EXHAUSTED_LOCK = threading.Lock()

_ROTATION_COUNTER = 0
_ROTATION_LOCK = threading.Lock()

DAILY_LIMIT_MARKERS = (
    "daily outbound call limit",
    "daily limit",
    "import your own twilio",
)


def _today_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _is_daily_limit_error(error_str: Optional[str]) -> bool:
    if not error_str:
        return False
    low = error_str.lower()
    return any(m in low for m in DAILY_LIMIT_MARKERS)


def _mark_exhausted(pid: str) -> None:
    with _EXHAUSTED_LOCK:
        _EXHAUSTED_TODAY[pid] = _today_utc()
    logger.info("VAPI phoneNumberId %s marked exhausted for %s", pid, _today_utc())


def _is_exhausted(pid: str) -> bool:
    with _EXHAUSTED_LOCK:
        return _EXHAUSTED_TODAY.get(pid) == _today_utc()


async def _discover_vapi_phone_numbers(private_key: str) -> list[str]:
    """GET /phone-number on VAPI to enumerate every number tied to the account.
    Cached for _VAPI_NUMBERS_TTL_SEC to avoid hammering VAPI on every dispatch."""
    global _VAPI_NUMBERS_CACHE
    import time
    now = time.time()
    async with _VAPI_NUMBERS_LOCK:
        if _VAPI_NUMBERS_CACHE and (now - _VAPI_NUMBERS_CACHE[0] < _VAPI_NUMBERS_TTL_SEC):
            return _VAPI_NUMBERS_CACHE[1]
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(
                    f"{VAPI_API_BASE}/phone-number",
                    headers={"Authorization": f"Bearer {private_key}"},
                )
                if r.status_code != 200:
                    logger.warning("VAPI phone-number list failed: %s %s", r.status_code, r.text[:200])
                    return _VAPI_NUMBERS_CACHE[1] if _VAPI_NUMBERS_CACHE else []
                data = r.json()
                ids = [n["id"] for n in data if isinstance(n, dict) and n.get("id")]
        except Exception:
            logger.exception("VAPI phone-number discovery failed")
            return _VAPI_NUMBERS_CACHE[1] if _VAPI_NUMBERS_CACHE else []
        _VAPI_NUMBERS_CACHE = (now, ids)
        return ids


async def _all_phone_number_ids(private_key: Optional[str]) -> list[str]:
    """Env override > VAPI account auto-discovery. Used by /config to show
    everything available (regardless of exhaustion)."""
    explicit = _env_phone_number_ids()
    if explicit:
        return explicit
    if not private_key:
        return []
    return await _discover_vapi_phone_numbers(private_key)


async def _available_phone_number_ids(private_key: Optional[str]) -> list[str]:
    """All phoneNumberIds NOT marked exhausted today."""
    ids = await _all_phone_number_ids(private_key)
    return [pid for pid in ids if not _is_exhausted(pid)]


async def _pick_phone_number_id(private_key: Optional[str]) -> Optional[str]:
    """Round-robin across the currently-available (non-exhausted) numbers."""
    global _ROTATION_COUNTER
    ids = await _available_phone_number_ids(private_key)
    if not ids:
        return None
    with _ROTATION_LOCK:
        pid = ids[_ROTATION_COUNTER % len(ids)]
        _ROTATION_COUNTER += 1
    return pid


def _vapi_env() -> dict[str, Any]:
    return {
        "private_key":  os.getenv("VAPI_PRIVATE_KEY"),
        "assistant_id": os.getenv("VAPI_ASSISTANT_ID"),
    }


def _to_e164(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if 10 <= len(digits) <= 15:
        return f"+{digits}"
    return None


# ── Retailer selection ────────────────────────────────────────────────────────

def _last10(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else None


async def _recently_contacted_phones(cooldown_hours: int) -> set[str]:
    """Return last-10-digit phone keys for retailers we successfully talked to
    within the cooldown window. A call counts as "talked to" when we got
    structured data back (has_game set), or the human-side duration suggests
    real conversation (>= 15s)."""
    if cooldown_hours <= 0:
        return set()
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT to_phone
               FROM vapi_calls
               WHERE received_at > NOW() - ($1::int || ' hours')::interval
                 AND to_phone IS NOT NULL
                 AND (has_game IS NOT NULL
                      OR confidence IS NOT NULL
                      OR (duration_sec IS NOT NULL AND duration_sec >= 15))""",
            cooldown_hours,
        )
    return {_last10(r["to_phone"]) for r in rows if _last10(r["to_phone"])}


async def _last_call_lookup() -> dict[str, dict]:
    """Map last-10-digit phone → {last_called_at, last_talked} across all
    vapi_calls history. Used to annotate the selection so the UI can show
    'last called X days ago' next to each retailer."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (RIGHT(REGEXP_REPLACE(to_phone, '[^0-9]', '', 'g'), 10))
                   RIGHT(REGEXP_REPLACE(to_phone, '[^0-9]', '', 'g'), 10) AS phone10,
                   received_at,
                   (has_game IS NOT NULL
                    OR confidence IS NOT NULL
                    OR (duration_sec IS NOT NULL AND duration_sec >= 15)) AS talked
            FROM vapi_calls
            WHERE to_phone IS NOT NULL
            ORDER BY 1, received_at DESC
        """)
    return {
        r["phone10"]: {"last_called_at": r["received_at"], "last_talked": r["talked"]}
        for r in rows if r["phone10"]
    }


async def _all_scored_candidates(state: str) -> list[dict]:
    """Return ALL callable candidates for `state`, score-sorted (desc) for
    MA/AZ, city/name for others. Phone-required. No cooldown filtering."""
    state = state.upper()

    if state == "MA":
        from backend.ma_scorer import load_and_score_async
        async with get_pool().acquire() as conn:
            scored = await load_and_score_async(conn)
        scored = [r for r in scored if r.get("phone")]
        scored.sort(key=lambda r: r.get("score", 0) or 0, reverse=True)
        return [{
            "external_id": str(r["id"]),
            "state_code":  "MA",
            "name":        r.get("name") or "",
            "city":        r.get("city") or "",
            "phone":       r.get("phone"),
            "score":       r.get("score"),
            "latitude":    r.get("latitude"),
            "longitude":   r.get("longitude"),
        } for r in scored]

    if state == "AZ":
        from backend.az_scorer import load_and_score_async
        async with get_pool().acquire() as conn:
            scored = await load_and_score_async(conn)
        scored = [r for r in scored if r.get("phone")]
        scored.sort(key=lambda r: r.get("score", 0) or 0, reverse=True)
        return [{
            "external_id": str(r["id"]),
            "state_code":  "AZ",
            "name":        r.get("name") or "",
            "city":        r.get("city") or "",
            "phone":       r.get("phone"),
            "score":       r.get("score"),
            "latitude":    r.get("latitude"),
            "longitude":   r.get("longitude"),
        } for r in scored]

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT external_id, state_code, name, city, phone, latitude, longitude
               FROM state_retailers
               WHERE state_code = $1 AND is_active = TRUE
                 AND phone IS NOT NULL AND phone <> ''
               ORDER BY city NULLS LAST, name""",
            state,
        )
    return [dict(r) | {"score": None} for r in rows]


async def _inventory_updated_ids(state: str) -> set[str]:
    """retailer_id values that have ever produced an inventory_reports row from
    a VAPI call (any game). Used for the 'Inventory updated' badge."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT retailer_id
               FROM inventory_reports
               WHERE source = 'vapi_call'
                 AND retailer_id IS NOT NULL
                 AND retailer_id <> ''"""
        )
    return {r["retailer_id"] for r in rows if r["retailer_id"]}


async def _annotate_candidates(
    candidates: list[dict],
    cooldown_hours: int,
) -> list[dict]:
    """Add last_called_at / last_talked / called_within_window / inventory_updated
    to each candidate."""
    cooldown_phones = await _recently_contacted_phones(cooldown_hours) if cooldown_hours > 0 else set()
    last_call_map   = await _last_call_lookup()
    inv_updated_ids = await _inventory_updated_ids("")
    out: list[dict] = []
    for r in candidates:
        ph10 = _last10(r.get("phone"))
        hist = last_call_map.get(ph10 or "")
        eid  = r.get("external_id")
        out.append({
            **r,
            "last_called_at":       hist["last_called_at"].isoformat() if hist and hist["last_called_at"] else None,
            "last_talked":          bool(hist["last_talked"]) if hist else False,
            "called_within_window": bool(ph10 and ph10 in cooldown_phones),
            "inventory_updated":    bool(eid and eid in inv_updated_ids),
        })
    return out


async def _select_scored_retailers(
    state: str,
    max_stores: int,
    cooldown_hours: int = 168,  # 7 days default
) -> tuple[list[dict], int]:
    """Top-N selection with cooldown filtering. Returns (selected, excluded_count)."""
    candidates = await _all_scored_candidates(state)
    annotated  = await _annotate_candidates(candidates, cooldown_hours)
    out: list[dict] = []
    excluded = 0
    for r in annotated:
        if r["called_within_window"]:
            excluded += 1
            continue
        out.append(r)
        if max_stores and len(out) >= max_stores:
            break
    return out, excluded


def _format_price(price) -> str:
    if price is None or price == "":
        return ""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return ""
    return f"${int(p)}" if p == int(p) else f"${p:.2f}"


def _build_tickets_text(tickets: list[dict]) -> str:
    """Turn [{name, price}, ...] into 'Fabulous Fortune ($10)\\n300X ($30)'."""
    lines = []
    for t in tickets or []:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        price_str = _format_price(t.get("price"))
        lines.append(f"{name} ({price_str})" if price_str else name)
    return "\n".join(lines)


def _tickets_label(tickets: list[dict]) -> str:
    """Short human label for storing in vapi_calls.game_name. E.g. '300X, Fabulous Fortune'."""
    names = [(t.get("name") or "").strip() for t in (tickets or [])]
    names = [n for n in names if n]
    return ", ".join(names)


async def _dispatch_calls(
    targets: list[dict],
    tickets: list[dict],
    env: dict,
) -> tuple[list[dict], list[dict]]:
    """POSTs to VAPI for each target. Inserts placeholder vapi_calls rows for
    successful dispatches so the dashboard reflects them immediately.

    Returns (results, skipped).
    """
    tickets_text = _build_tickets_text(tickets)
    tickets_label = _tickets_label(tickets)
    valid: list[dict] = []
    skipped: list[dict] = []
    for t in targets:
        e164 = _to_e164(t.get("phone"))
        if not e164:
            skipped.append({"name": t.get("name"), "reason": "no valid phone"})
            continue
        valid.append({**t, "phone_e164": e164})

    sem = asyncio.Semaphore(CONCURRENCY)
    results: list[dict] = []
    placeholders: list[dict] = []

    async with httpx.AsyncClient(
        base_url=VAPI_API_BASE,
        timeout=30.0,
        headers={
            "Authorization": f"Bearer {env['private_key']}",
            "Content-Type": "application/json",
        },
    ) as client:

        async def _one(t: dict):
            # Try up to (number_count) attempts: on each daily-cap error, mark
            # that number exhausted and pick a different one. Bounded by the
            # number of available IDs so we don't loop forever.
            tried_pids: set[str] = set()
            last_error: Optional[str] = None
            last_status: int = 0
            while True:
                pid = await _pick_phone_number_id(env["private_key"])
                if not pid or pid in tried_pids:
                    # No more numbers to try
                    results.append({
                        "name":    t.get("name"),
                        "city":    t.get("city"),
                        "ok":      False,
                        "status":  last_status,
                        "call_id": None,
                        "error":   last_error or "No available VAPI phone numbers (all exhausted)",
                    })
                    return
                tried_pids.add(pid)

                payload = {
                    "assistantId":    env["assistant_id"],
                    "phoneNumberId":  pid,
                    "customer":       {"number": t["phone_e164"]},
                    "assistantOverrides": {
                        "variableValues": {
                            "store_id":       t.get("external_id") or "",
                            "store_name":     t.get("name") or "",
                            "store_city":     t.get("city") or "",
                            "store_phone":    t["phone_e164"],
                            "state_code":     t.get("state_code") or "",
                            "ticketsToCheck": tickets_text,
                        },
                    },
                }

                async with sem:
                    try:
                        resp = await client.post("/call", json=payload)
                    except Exception as exc:
                        logger.exception("VAPI dispatch failed for %s", t.get("name"))
                        results.append({
                            "name":    t.get("name"),
                            "city":    t.get("city"),
                            "ok":      False,
                            "status":  0,
                            "call_id": None,
                            "error":   str(exc),
                        })
                        return

                ok = 200 <= resp.status_code < 300
                data: dict[str, Any] = {}
                try:
                    data = resp.json()
                except Exception:
                    data = {"text": resp.text[:300]}
                err_msg = None if ok else (data.get("message") or data.get("text") or "unknown")

                # Daily-cap error → mark this number dead for today, try next
                if not ok and _is_daily_limit_error(err_msg):
                    _mark_exhausted(pid)
                    last_error  = err_msg
                    last_status = resp.status_code
                    continue

                call_id = data.get("id") if ok else None
                results.append({
                    "name":    t.get("name"),
                    "city":    t.get("city"),
                    "ok":      ok,
                    "status":  resp.status_code,
                    "call_id": call_id,
                    "error":   err_msg,
                })
                if ok and call_id:
                    placeholders.append({
                        "vapi_call_id":         call_id,
                        "to_phone":             t["phone_e164"],
                        "state_code":           t.get("state_code"),
                        "retailer_external_id": t.get("external_id"),
                        "retailer_name":        t.get("name"),
                        "retailer_city":        t.get("city"),
                        "game_name":            tickets_label,
                        "game_price":           None,
                        "game_number":          None,
                    })
                return

        await asyncio.gather(*(_one(t) for t in valid))

    if placeholders:
        await _insert_placeholders(placeholders)

    return results, skipped


async def _insert_placeholders(rows: list[dict]) -> None:
    """Insert pending vapi_calls rows so the dashboard can show in-flight calls
    before the end-of-call webhook arrives. The webhook UPSERTs by vapi_call_id
    so timestamps + structured fields get filled in later."""
    now = dt.datetime.now(dt.timezone.utc)
    pool = get_pool()
    async with pool.acquire() as conn:
        for r in rows:
            try:
                await conn.execute(
                    """
                    INSERT INTO vapi_calls (
                        vapi_call_id, started_at, to_phone, state_code,
                        retailer_external_id, retailer_name, retailer_city,
                        game_name, game_price, game_number, raw_payload
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                    ON CONFLICT (vapi_call_id) DO NOTHING
                    """,
                    r["vapi_call_id"], now, r["to_phone"], r["state_code"],
                    r["retailer_external_id"], r["retailer_name"], r["retailer_city"],
                    r["game_name"], r["game_price"], r["game_number"],
                    json.dumps({"source": "dispatch_placeholder"}),
                )
            except Exception:
                logger.exception("Failed to insert placeholder for call %s", r.get("vapi_call_id"))


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/config")
async def vapi_config(_user: dict = Depends(require_admin)):
    env = _vapi_env()
    ids = env["phone_number_ids"]
    return {
        "configured":         bool(env["private_key"] and env["assistant_id"] and ids),
        "has_private_key":    bool(env["private_key"]),
        "has_assistant_id":   bool(env["assistant_id"]),
        "has_phone_number":   bool(ids),
        "assistant_id":       env["assistant_id"],
        "phone_number_ids":   ids,
        "phone_number_count": len(ids),
    }


@router.get("/diagnostics")
async def vapi_diagnostics(request: Request, _user: dict = Depends(require_admin)):
    """Webhook health check — tells you if VAPI's end-of-call reports are
    actually reaching this server, and gives you the exact URL to configure
    in the VAPI assistant if they're not."""
    async with get_pool().acquire() as conn:
        last_webhook = await conn.fetchval("""
            SELECT MAX(received_at)
            FROM vapi_calls
            WHERE ended_at IS NOT NULL OR transcript IS NOT NULL OR ended_reason IS NOT NULL
        """)
        last_any = await conn.fetchval("SELECT MAX(received_at) FROM vapi_calls")
        stuck = await conn.fetchval("""
            SELECT COUNT(*)
            FROM vapi_calls
            WHERE ended_at IS NULL
              AND received_at < NOW() - INTERVAL '5 min'
        """)

    base = str(request.base_url).rstrip("/")
    return {
        "expected_webhook_url":      f"{base}/api/vapi/webhook",
        "webhook_secret_configured": bool(os.getenv("VAPI_WEBHOOK_SECRET")),
        "last_webhook_received_at":  last_webhook.isoformat() if last_webhook else None,
        "last_call_dispatched_at":   last_any.isoformat() if last_any else None,
        "stuck_in_flight":           int(stuck or 0),
    }


@router.get("/stats")
async def vapi_stats(_user: dict = Depends(require_admin)):
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
              COUNT(*)                                                        AS total_calls,
              COUNT(*) FILTER (WHERE has_game = TRUE)                         AS hits,
              COUNT(*) FILTER (WHERE ended_at IS NULL
                                 AND received_at > NOW() - INTERVAL '15 min') AS in_flight,
              COUNT(*) FILTER (WHERE received_at > NOW() - INTERVAL '24 hours') AS calls_today,
              COUNT(*) FILTER (WHERE is_voicemail = TRUE)                     AS voicemails,
              COUNT(*) FILTER (WHERE is_voicemail = TRUE
                                 AND received_at > NOW() - INTERVAL '24 hours') AS voicemails_today
            FROM vapi_calls
        """)
    return {
        "total_calls":      int(row["total_calls"] or 0),
        "hits":             int(row["hits"] or 0),
        "in_flight":        int(row["in_flight"] or 0),
        "calls_today":      int(row["calls_today"] or 0),
        "voicemails":       int(row["voicemails"] or 0),
        "voicemails_today": int(row["voicemails_today"] or 0),
    }


@router.get("/retailers")
async def vapi_retailers(
    _user: dict = Depends(require_admin),
    state: Optional[str] = Query(None, description="2-letter state code"),
    search: Optional[str] = Query(None, description="Match name or city (ILIKE)"),
    limit: int = Query(200, ge=1, le=1000),
    only_with_phone: bool = Query(True),
):
    clauses = ["is_active = TRUE"]
    args: list = []
    if state:
        args.append(state.upper().strip())
        clauses.append(f"state_code = ${len(args)}")
    if only_with_phone:
        clauses.append("phone IS NOT NULL AND phone <> ''")
    if search:
        args.append(f"%{search.strip()}%")
        clauses.append(f"(name ILIKE ${len(args)} OR city ILIKE ${len(args)})")
    args.append(limit)
    sql = f"""
        SELECT id, state_code, external_id, name, city, address, zip_code, phone
        FROM state_retailers
        WHERE {' AND '.join(clauses)}
        ORDER BY state_code, city NULLS LAST, name
        LIMIT ${len(args)}
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return {"retailers": [dict(r) for r in rows], "count": len(rows)}


@router.get("/states")
async def vapi_states(_user: dict = Depends(require_admin)):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT state_code,
                   COUNT(*) FILTER (WHERE phone IS NOT NULL AND phone <> '') AS with_phone,
                   COUNT(*)                                                  AS total
            FROM state_retailers
            WHERE is_active = TRUE
            GROUP BY state_code
            HAVING COUNT(*) FILTER (WHERE phone IS NOT NULL AND phone <> '') > 0
            ORDER BY state_code
        """)
    return {"states": [dict(r) for r in rows]}


class TicketSpec(BaseModel):
    name: str
    price: Optional[float] = None


class DispatchBody(BaseModel):
    retailer_ids: list[int] = Field(..., min_length=1, max_length=MAX_BATCH)
    tickets: list[TicketSpec] = Field(..., min_length=1, max_length=20)
    dry_run: bool = False


@router.post("/dispatch")
async def vapi_dispatch(body: DispatchBody, _user: dict = Depends(require_admin)):
    """Ad-hoc dispatch by explicit retailer_ids (used by checkbox-list UIs)."""
    env = _vapi_env()
    if not body.dry_run and not all(env.values()):
        missing = [k for k, v in env.items() if not v]
        raise HTTPException(status_code=400, detail=f"VAPI not configured — missing env: {', '.join(missing)}")

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT external_id, state_code, name, city, phone
               FROM state_retailers
               WHERE id = ANY($1::int[]) AND is_active = TRUE""",
            body.retailer_ids,
        )
    if not rows:
        raise HTTPException(status_code=404, detail="No matching retailers found")

    targets = [dict(r) for r in rows]
    tickets = [t.model_dump() for t in body.tickets]
    if body.dry_run:
        return {
            "dry_run": True,
            "would_call": sum(1 for t in targets if _to_e164(t.get("phone"))),
            "tickets_text": _build_tickets_text(tickets),
            "preview": targets[:25],
        }

    results, skipped = await _dispatch_calls(targets, tickets, env)
    success = sum(1 for r in results if r["ok"])
    return {
        "dispatched":   success,
        "failed":       len(results) - success,
        "skipped":      skipped,
        "results":      results,
        "tickets_text": _build_tickets_text(tickets),
    }


class CampaignBody(BaseModel):
    state: str = Field(..., min_length=2, max_length=2)
    tickets: list[TicketSpec] = Field(..., min_length=1, max_length=20)
    max_stores: int = Field(default=100, ge=1, le=MAX_BATCH)
    cooldown_hours: int = Field(default=168, ge=0, le=8760)  # 7 days default
    dry_run: bool = False


@router.post("/dispatch_campaign")
async def vapi_dispatch_campaign(body: CampaignBody, _user: dict = Depends(require_admin)):
    """Campaign-style dispatch: pick top-N retailers for a state and fire.
    Skips retailers we already successfully talked to within cooldown_hours."""
    env = _vapi_env()
    if not body.dry_run and not all(env.values()):
        missing = [k for k, v in env.items() if not v]
        raise HTTPException(status_code=400, detail=f"VAPI not configured — missing env: {', '.join(missing)}")

    targets, excluded = await _select_scored_retailers(
        body.state.upper(), body.max_stores, body.cooldown_hours
    )
    if not targets:
        raise HTTPException(
            status_code=404,
            detail=(f"No callable retailers found for {body.state} "
                    f"(excluded {excluded} recently-contacted)"),
        )

    tickets = [t.model_dump() for t in body.tickets]
    if body.dry_run:
        preview = [{
            "name":           t["name"],
            "city":           t["city"],
            "score":          t.get("score"),
            "phone":          t["phone"],
            "last_called_at": t.get("last_called_at"),
            "last_talked":    t.get("last_talked"),
        } for t in targets[:25]]
        return {
            "dry_run":             True,
            "selected":            len(targets),
            "excluded_cooldown":   excluded,
            "would_call":          sum(1 for t in targets if _to_e164(t.get("phone"))),
            "tickets_text":        _build_tickets_text(tickets),
            "preview":             preview,
        }

    results, skipped = await _dispatch_calls(targets, tickets, env)
    success = sum(1 for r in results if r["ok"])
    return {
        "selected":          len(targets),
        "excluded_cooldown": excluded,
        "dispatched":        success,
        "failed":            len(results) - success,
        "skipped":           skipped,
        "tickets_text":      _build_tickets_text(tickets),
        "results":           results[:50],
    }


@router.get("/candidates")
async def vapi_candidates(
    _user: dict = Depends(require_admin),
    state: str = Query(..., min_length=2, max_length=2),
    cooldown_hours: int = Query(168, ge=0, le=8760),
):
    """All callable retailers for `state` (score-sorted), each annotated with
    last_called_at / last_talked / called_within_window so the UI can show
    'Called ever' and 'Called within recall window' badges."""
    candidates = await _all_scored_candidates(state.upper())
    annotated  = await _annotate_candidates(candidates, cooldown_hours)
    return {
        "state":          state.upper(),
        "cooldown_hours": cooldown_hours,
        "total":          len(annotated),
        "candidates":     annotated,
    }


class DispatchSelectedBody(BaseModel):
    state: str = Field(..., min_length=2, max_length=2)
    selected_external_ids: list[str] = Field(..., min_length=1, max_length=MAX_BATCH)
    tickets: list[TicketSpec] = Field(..., min_length=1, max_length=20)
    dry_run: bool = False


@router.post("/dispatch_selected")
async def vapi_dispatch_selected(body: DispatchSelectedBody, _user: dict = Depends(require_admin)):
    """Dispatch to an explicitly-chosen, explicitly-ordered list of retailers
    identified by their `external_id` (the same key used as VAPI `store_id`).
    Order in `selected_external_ids` is the dial order."""
    env = _vapi_env()
    if not body.dry_run and not all(env.values()):
        missing = [k for k, v in env.items() if not v]
        raise HTTPException(status_code=400, detail=f"VAPI not configured — missing env: {', '.join(missing)}")

    candidates = await _all_scored_candidates(body.state.upper())
    by_id = {c["external_id"]: c for c in candidates}
    targets: list[dict] = []
    missing_ids: list[str] = []
    for eid in body.selected_external_ids:
        c = by_id.get(eid)
        if c:
            targets.append(c)
        else:
            missing_ids.append(eid)
    if not targets:
        raise HTTPException(status_code=404, detail="No matching retailers found for the selected IDs")

    tickets = [t.model_dump() for t in body.tickets]
    if body.dry_run:
        preview = [{
            "external_id": t["external_id"],
            "name":        t["name"],
            "city":        t["city"],
            "score":       t.get("score"),
            "phone":       t["phone"],
        } for t in targets[:25]]
        return {
            "dry_run":      True,
            "selected":     len(targets),
            "would_call":   sum(1 for t in targets if _to_e164(t.get("phone"))),
            "missing_ids":  missing_ids,
            "tickets_text": _build_tickets_text(tickets),
            "preview":      preview,
        }

    results, skipped = await _dispatch_calls(targets, tickets, env)
    success = sum(1 for r in results if r["ok"])
    return {
        "selected":     len(targets),
        "dispatched":   success,
        "failed":       len(results) - success,
        "skipped":      skipped,
        "missing_ids":  missing_ids,
        "tickets_text": _build_tickets_text(tickets),
        "results":      results[:50],
    }


class TestCallBody(BaseModel):
    phone: str
    tickets: list[TicketSpec] = Field(..., min_length=1, max_length=20)
    as_retailer_id: Optional[int] = None  # state_retailers.id — simulate this store


@router.post("/test_call")
async def vapi_test_call(body: TestCallBody, _user: dict = Depends(require_admin)):
    """Dispatch a single test call to an arbitrary number. Optionally simulate
    a real retailer so the assistant prompt sounds like a production call.
    The external_id is always prefixed with 'test' so the webhook skips the
    inventory_reports mirror and the call doesn't pollute production data."""
    env = _vapi_env()
    if not all(env.values()):
        missing = [k for k, v in env.items() if not v]
        raise HTTPException(status_code=400, detail=f"VAPI not configured — missing env: {', '.join(missing)}")

    e164 = _to_e164(body.phone)
    if not e164:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    sim_name = "Test Call"
    sim_city = ""
    sim_state = ""
    sim_external = "test"  # generic test → webhook skips inventory mirror

    if body.as_retailer_id is not None:
        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """SELECT state_code, external_id, name, city
                   FROM state_retailers WHERE id = $1""",
                body.as_retailer_id,
            )
        if not row:
            raise HTTPException(status_code=404, detail="as_retailer_id not found")
        sim_name = row["name"] or sim_name
        sim_city = row["city"] or ""
        sim_state = row["state_code"] or ""
        # Simulate-as-store IS the simulated store from the webhook's POV:
        # use the real external_id so the per-ticket inventory mirror fires
        # for this retailer. That's the user's explicit ask — "I want these to
        # update inventory reports! that is the point also."
        sim_external = row["external_id"]

    target = {
        "external_id": sim_external,
        "state_code":  sim_state,
        "name":        sim_name,
        "city":        sim_city,
        "phone":       e164,
    }
    tickets = [t.model_dump() for t in body.tickets]
    results, _ = await _dispatch_calls([target], tickets, env)
    r = results[0] if results else {"ok": False, "error": "no result"}
    if not r["ok"]:
        raise HTTPException(status_code=502, detail=f"VAPI dispatch failed: {r.get('error')}")
    return {
        "ok":              True,
        "call_id":         r["call_id"],
        "simulated_store": sim_name,
        "simulated_city":  sim_city,
        "tickets_text":    _build_tickets_text(tickets),
    }
