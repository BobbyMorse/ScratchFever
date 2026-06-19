"""
ScratchFrenzy API — FastAPI backend.
Serves game data and triggers scrapes.
"""
import asyncio
import datetime
import logging
import os

from dotenv import load_dotenv
load_dotenv()
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.database import (
    init_db, init_retailer_db, get_pool, get_all_games, get_game_detail, get_states_summary,
    add_inventory_report, get_recent_inventory_reports, get_recent_prize_claims,
    get_reported_wins, get_reported_wins_for_map, clear_games_cache,
    get_weekly_sales, get_second_chance,
)
from backend.caller.db import init_caller_db
from backend.caller.webhook import router as caller_webhook_router
from backend.caller.api import router as caller_api_router
from backend.caller.vapi_db import init_vapi_db
from backend.caller.vapi_webhook import router as vapi_router, analysis_poller_loop
from backend.caller.vapi_dispatch import router as vapi_dispatch_router
from backend.caller.vapi_queue import (
    init_vapi_queue_db, vapi_queue_worker_loop, router as vapi_queue_router,
)
from backend.users import init_users_db, seed_admin, require_member, require_admin
from backend.auth_api import router as auth_router
from backend import analytics
from backend.retailer_api import router as retailer_router, public_router as retailer_public_router, admin_router as retailer_admin_router
from backend.plays_api import router as plays_router
from backend.tickets_api import router as tickets_router
from backend.revenuecat_webhook import router as revenuecat_router
from backend.billing_api import router as billing_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
scrape_status = {"running": False, "last_run": None, "last_results": [], "current_state": None}


# Scheduler is gated on DISABLE_SCHEDULER env var (Phase 3 worker-split prep).
# Default unset → scheduler runs in-process exactly as before. Set to "1" on
# the API service once a dedicated worker service is running scrapers.
SCHEDULER_DISABLED = os.environ.get("DISABLE_SCHEDULER") == "1"


async def scheduled_scrape():
    """Compatibility wrapper for the manual scrape endpoint (/api/scrape).
    Delegates to backend.scheduler_jobs so worker + API share one code path."""
    from backend.scheduler_jobs import run_games_scrape_cycle
    if scrape_status["running"]:
        logger.info("Scrape already running, skipping")
        return
    scrape_status["running"] = True
    scrape_status["current_state"] = None
    try:
        def _on_state(code, name):
            scrape_status["current_state"] = {"code": code, "name": name}
        results = await run_games_scrape_cycle(on_state=_on_state)
        scrape_status["last_results"] = results
        scrape_status["last_run"] = datetime.datetime.utcnow().isoformat()
    except Exception as e:
        logger.error("Scrape cycle failed: %s", e, exc_info=True)
    finally:
        scrape_status["running"] = False


async def check_and_run_stale_retailers():
    """Wrapper for manual triggers (admin endpoints)."""
    from backend.scheduler_jobs import run_retailer_freshness_cycle
    try:
        results = await run_retailer_freshness_cycle()
        if results:
            logger.info("Retailer scrape complete: %s", results)
    except Exception as e:
        logger.error("Retailer staleness check failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    analytics.init()
    logger.info("startup: init_db ...")
    await init_db()
    logger.info("startup: init_caller_db ...")
    await init_caller_db()
    logger.info("startup: init_vapi_db ...")
    await init_vapi_db()
    logger.info("startup: init_vapi_queue_db ...")
    await init_vapi_queue_db()
    logger.info("startup: init_users_db ...")
    await init_users_db()
    logger.info("startup: init_retailer_db ...")
    await init_retailer_db()
    logger.info("startup: seed_admin ...")
    await seed_admin()
    logger.info("startup: schema init complete")

    # Durable VAPI analysis poller — heals rows whose webhook backstop
    # didn't survive a process restart. Starts on every boot, no-ops when
    # VAPI_PRIVATE_KEY is missing. Tracked so we can cancel on shutdown.
    vapi_poller_task = asyncio.create_task(analysis_poller_loop())

    # Concurrency-gated dispatch worker — pulls pending vapi_call_queue rows
    # and fires them N at a time (default 2). DISABLE_VAPI_QUEUE_WORKER=1 to
    # skip on worker-split deployments where only one service should pop the
    # queue. Webhooks land on the API, so keep this on the API process.
    if os.environ.get("DISABLE_VAPI_QUEUE_WORKER") == "1":
        logger.info("DISABLE_VAPI_QUEUE_WORKER=1 — vapi_queue worker NOT running in this process")
        vapi_queue_task = None
    else:
        vapi_queue_task = asyncio.create_task(vapi_queue_worker_loop())

    if SCHEDULER_DISABLED:
        logger.info("DISABLE_SCHEDULER=1 — scrapers will NOT run in this process "
                    "(assuming a dedicated worker service is handling them)")
    else:
        from backend.scheduler_jobs import register_jobs, ensure_playwright_browsers
        kick_games_now = register_jobs(
            scheduler,
            status_dict=scrape_status,
            on_winners_finish=lambda: _reported_wins_cache.clear(),
        )
        scheduler.start()

        # Delay heavy startup work so Railway health checks pass before scraping begins
        STARTUP_DELAY = 90  # seconds after boot before first scrape/cache-warm

        async def _delayed_startup():
            await asyncio.sleep(STARTUP_DELAY)
            await asyncio.to_thread(ensure_playwright_browsers)
            try:
                from backend.ma_scorer import load_and_score_async as warm_ma
                from backend.az_scorer import load_and_score_async as warm_az
                pool = get_pool()
                async with pool.acquire() as conn:
                    await warm_ma(conn)
                async with pool.acquire() as conn:
                    await warm_az(conn)
                logger.info("Retailer caches warmed")
            except Exception as e:
                logger.warning("Cache warm failed: %s", e)
            await check_and_run_stale_retailers()
            await kick_games_now()

        asyncio.create_task(_delayed_startup())

    yield
    vapi_poller_task.cancel()
    if vapi_queue_task:
        vapi_queue_task.cancel()
    if not SCHEDULER_DISABLED:
        scheduler.shutdown()
    analytics.shutdown()


app = FastAPI(title="ScratchFrenzy", description="Scratch-off lottery EV tracker", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)

_DEFAULT_CORS_ORIGINS = [
    "https://scratchfrenzy.app",
    "https://www.scratchfrenzy.app",
    "https://api.scratchfrenzy.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
_cors_env = os.getenv("CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else _DEFAULT_CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"^exp://.*|^https://.*\.expo\.dev$",
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    allow_credentials=True,
)

app.include_router(auth_router)
app.include_router(caller_webhook_router)
app.include_router(caller_api_router)
app.include_router(vapi_router)
app.include_router(vapi_dispatch_router)
app.include_router(vapi_queue_router)
app.include_router(retailer_router)
app.include_router(retailer_public_router)
app.include_router(retailer_admin_router)
app.include_router(plays_router)
app.include_router(tickets_router)
app.include_router(revenuecat_router)
app.include_router(billing_router)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/about", include_in_schema=False)
async def about():
    return FileResponse(os.path.join(FRONTEND_DIR, "about.html"))


@app.get("/privacy", include_in_schema=False)
async def privacy():
    return FileResponse(os.path.join(FRONTEND_DIR, "privacy.html"))


@app.get("/terms", include_in_schema=False)
async def terms():
    return FileResponse(os.path.join(FRONTEND_DIR, "terms.html"))


@app.get("/contact", include_in_schema=False)
async def contact():
    return FileResponse(os.path.join(FRONTEND_DIR, "contact.html"))


@app.get("/blog", include_in_schema=False)
async def blog_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "blog", "index.html"))


@app.get("/blog/{slug}", include_in_schema=False)
async def blog_post(slug: str):
    # Allow only safe slug characters; map to a file in /frontend/blog/
    safe = "".join(c for c in slug if c.isalnum() or c in ("-", "_"))
    if not safe or safe != slug:
        return FileResponse(os.path.join(FRONTEND_DIR, "blog", "index.html"))
    path = os.path.join(FRONTEND_DIR, "blog", f"{safe}.html")
    if os.path.isfile(path):
        return FileResponse(path)
    return FileResponse(os.path.join(FRONTEND_DIR, "blog", "index.html"))


@app.get("/delete-account", include_in_schema=False)
async def delete_account_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "delete-account.html"))


@app.get("/retailer", include_in_schema=False)
async def retailer_dashboard():
    return FileResponse(os.path.join(FRONTEND_DIR, "retailer.html"))


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin.html"))


@app.get("/store/{retailer_id}", include_in_schema=False)
async def store_page(retailer_id: str):
    # Public store page — the retailer_id is parsed client-side from window.location.
    # We just serve the same HTML for every store id.
    return FileResponse(os.path.join(FRONTEND_DIR, "store.html"))


# ── Admin: create retailer account ────────────────────────────────────────────

class CreateRetailerBody(BaseModel):
    email: str
    password: str
    retailer_id: str
    state_code: str = "MA"
    store_name: str
    city: Optional[str] = None
    zip_code: Optional[str] = None
    phone: Optional[str] = None


@app.post("/api/admin/retailers")
async def admin_create_retailer(body: CreateRetailerBody, user: dict = Depends(require_admin)):
    from backend.users import create_user
    try:
        new_user = await create_user(body.email.strip(), body.password, role="retailer")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO retailer_profiles
               (user_id, retailer_id, state_code, store_name, city, zip, phone, verified)
               VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE)""",
            new_user["id"], body.retailer_id.strip(), body.state_code.upper().strip(),
            body.store_name.strip(), body.city, body.zip_code, body.phone,
        )
    return {"message": "Retailer account created", "user_id": new_user["id"], "email": body.email}


@app.get("/api/admin/health")
async def admin_health(user: dict = Depends(require_admin)):
    from backend.scraper.runner import ALL_SCRAPERS
    from backend.retailer_scrapers.runner import SCRAPERS as RETAILER_STATES

    all_known = {cls.state_code: cls.state_name for cls in ALL_SCRAPERS}
    all_known.setdefault("AL", "Alabama")
    all_known.setdefault("ME", "Maine")
    retailer_state_set = set(RETAILER_STATES)

    async with get_pool().acquire() as conn:
        game_rows = await conn.fetch("""
            SELECT
                state_code, state_name,
                COUNT(*) AS games_in_db,
                MAX(scraped_at) AS last_scraped,
                ROUND(100.0 * COUNT(image_url) / COUNT(*), 1) AS image_pct,
                ROUND(100.0 * COUNT(ev) / COUNT(*), 1) AS ev_pct,
                ROUND(AVG(CASE WHEN ev IS NOT NULL THEN return_pct END)::numeric, 1) AS avg_return,
                ROUND(100.0 * COUNT(CASE WHEN tickets_remaining > 0 THEN 1 END) / COUNT(*), 1) AS remaining_pct,
                COUNT(CASE WHEN tickets_remaining = 0 THEN 1 END) AS zero_remaining_count
            FROM games
            WHERE is_active = TRUE
            GROUP BY state_code, state_name
            ORDER BY state_name
        """)
        log_rows = await conn.fetch("""
            SELECT DISTINCT ON (state_code)
                state_code, success, games_scraped, error_msg, ran_at
            FROM scrape_log
            ORDER BY state_code, ran_at DESC
        """)
        retailer_rows = await conn.fetch(
            "SELECT state_code, last_scraped_at, retailers_count FROM retailer_scrape_log"
        )

    games_by_state = {r["state_code"]: r for r in game_rows}
    log_by_state = {r["state_code"]: r for r in log_rows}
    retailer_by_state = {r["state_code"]: r for r in retailer_rows}

    states = []
    for state_code, state_name in all_known.items():
        game = games_by_state.get(state_code)
        log = log_by_state.get(state_code, {})
        ret = retailer_by_state.get(state_code)
        states.append({
            "state_code": state_code,
            "state_name": state_name,
            "games_in_db": game["games_in_db"] if game else 0,
            "last_scraped": game["last_scraped"].isoformat() if game and game["last_scraped"] else None,
            "image_pct": float(game["image_pct"] or 0) if game else 0,
            "ev_pct": float(game["ev_pct"] or 0) if game else 0,
            "avg_return": float(game["avg_return"] or 0) if game else 0,
            "remaining_pct": float(game["remaining_pct"] or 0) if game else 0,
            "zero_remaining_count": int(game["zero_remaining_count"] or 0) if game else 0,
            "last_scrape_success": log.get("success"),
            "last_scrape_games": log.get("games_scraped"),
            "last_scrape_error": log.get("error_msg"),
            "last_scrape_at": log["ran_at"].isoformat() if log.get("ran_at") else None,
            "has_retailer_scraper": state_code in retailer_state_set,
            "retailer_last_scraped": ret["last_scraped_at"].isoformat() if ret else None,
            "retailer_count": ret["retailers_count"] if ret else None,
        })

    states.sort(key=lambda s: s["state_name"])
    return {"states": states}


@app.get("/api/admin/health/winners")
async def admin_health_winners(user: dict = Depends(require_admin)):
    """Per-state health of the winners feeds (backend/scraper/winners/*).
    Surfaces last-scrape freshness, total/recent win counts, and geo-resolution
    rate so the admin dashboard can spot a feed that stopped returning data."""
    from backend.scraper.winners.runner import ALL_WINNERS_SCRAPERS

    configured = {cls.state_code: cls.state_name for cls in ALL_WINNERS_SCRAPERS}

    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                state_code,
                COUNT(*) AS wins_total,
                COUNT(*) FILTER (WHERE scraped_at > NOW() - INTERVAL '24 hours') AS wins_scraped_24h,
                COUNT(*) FILTER (WHERE claim_date > CURRENT_DATE - INTERVAL '7 days') AS wins_claimed_7d,
                COUNT(*) FILTER (WHERE claim_date > CURRENT_DATE - INTERVAL '30 days') AS wins_claimed_30d,
                MAX(scraped_at) AS last_scraped,
                MAX(claim_date) AS most_recent_claim,
                ROUND(100.0 * COUNT(*) FILTER (WHERE retailer_lat IS NOT NULL) / NULLIF(COUNT(*), 0), 1) AS geo_pct
            FROM reported_wins
            GROUP BY state_code
        """)
        # Per-state run log — distinguishes "scraper crashed" from "scraper
        # ran fine, source quiet" (both look identical from reported_wins alone).
        log_rows = await conn.fetch("""
            SELECT state_code, last_attempted_at, last_success_at,
                   rows_last_run, last_error
            FROM winners_scrape_log
        """)

    by_state = {r["state_code"]: r for r in rows}
    log_by = {r["state_code"]: r for r in log_rows}
    out = []
    for code, name in configured.items():
        r = by_state.get(code)
        lg = log_by.get(code)
        out.append({
            "state_code": code,
            "state_name": name,
            "wins_total": int(r["wins_total"]) if r else 0,
            "wins_scraped_24h": int(r["wins_scraped_24h"]) if r else 0,
            "wins_claimed_7d": int(r["wins_claimed_7d"]) if r else 0,
            "wins_claimed_30d": int(r["wins_claimed_30d"]) if r else 0,
            "last_scraped": r["last_scraped"].isoformat() if r and r["last_scraped"] else None,
            "most_recent_claim": r["most_recent_claim"].isoformat() if r and r["most_recent_claim"] else None,
            "geo_pct": float(r["geo_pct"] or 0) if r else 0.0,
            # Run-log fields. last_attempted_at advances on every cycle;
            # last_success_at only advances when the scraper finished without
            # raising. last_error is the most recent error string or null.
            "last_attempted_at": lg["last_attempted_at"].isoformat() if lg and lg["last_attempted_at"] else None,
            "last_success_at": lg["last_success_at"].isoformat() if lg and lg["last_success_at"] else None,
            "rows_last_run": int(lg["rows_last_run"]) if lg else None,
            "last_error": lg["last_error"] if lg else None,
        })
    out.sort(key=lambda s: s["state_name"])
    return {"states": out}


@app.get("/api/admin/health/retailers")
async def admin_health_retailers(user: dict = Depends(require_admin)):
    """Per-state health of the retailer-locator scrapers
    (backend/retailer_scrapers/*). Reflects retailer_scrape_log entries
    written by the staleness-driven daily job."""
    from backend.retailer_scrapers.runner import SCRAPERS as RETAILER_STATES
    from backend.scraper.runner import ALL_SCRAPERS

    state_names = {cls.state_code: cls.state_name for cls in ALL_SCRAPERS}
    state_names.setdefault("DC", "District of Columbia")

    async with get_pool().acquire() as conn:
        log_rows = await conn.fetch(
            "SELECT state_code, last_scraped_at, retailers_count FROM retailer_scrape_log"
        )

    log_by = {r["state_code"]: r for r in log_rows}

    out = []
    for code in RETAILER_STATES:
        row = log_by.get(code)
        out.append({
            "state_code": code,
            "state_name": state_names.get(code, code),
            "last_scraped": row["last_scraped_at"].isoformat() if row else None,
            "retailers_count": int(row["retailers_count"]) if row else None,
        })
    out.sort(key=lambda s: s["state_name"])
    return {"states": out}


# ── Admin: user management ────────────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_list_users(user: dict = Depends(require_admin)):
    """All users with role, status, last-login, and per-user activity counts.
    Joins inventory_reports by reporter_username for an at-a-glance contribution metric."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                u.id, u.email, u.username, u.role,
                u.created_at, u.last_login_at, u.login_count,
                u.pro_until, u.stripe_customer_id, u.stripe_subscription_id,
                COALESCE(ir.report_count, 0) AS inventory_reports
            FROM users u
            LEFT JOIN (
                SELECT reporter_username, COUNT(*) AS report_count
                FROM inventory_reports
                WHERE reporter_username IS NOT NULL
                GROUP BY reporter_username
            ) ir ON ir.reporter_username = u.username
            ORDER BY u.created_at DESC
        """)
        summary = await conn.fetchrow("""
            SELECT
                COUNT(*)                                                        AS total,
                COUNT(*) FILTER (WHERE role = 'admin')                          AS admins,
                COUNT(*) FILTER (WHERE role = 'member')                         AS members,
                COUNT(*) FILTER (WHERE role = 'retailer')                       AS retailers,
                COUNT(*) FILTER (WHERE pro_until IS NOT NULL AND pro_until > NOW()) AS pro,
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days')  AS new_7d,
                COUNT(*) FILTER (WHERE last_login_at > NOW() - INTERVAL '30 days') AS active_30d
            FROM users
        """)

    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    users = []
    for r in rows:
        pro_until = r["pro_until"]
        users.append({
            "id": r["id"],
            "email": r["email"],
            "username": r["username"],
            "role": r["role"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "last_login_at": r["last_login_at"].isoformat() if r["last_login_at"] else None,
            "login_count": r["login_count"] or 0,
            "pro_until": pro_until.isoformat() if pro_until else None,
            "is_pro": bool(pro_until and pro_until > now),
            "has_stripe": bool(r["stripe_customer_id"]),
            "stripe_subscription_id": r["stripe_subscription_id"],
            "inventory_reports": int(r["inventory_reports"] or 0),
        })
    return {
        "users": users,
        "summary": {
            "total": int(summary["total"] or 0),
            "admins": int(summary["admins"] or 0),
            "members": int(summary["members"] or 0),
            "retailers": int(summary["retailers"] or 0),
            "pro": int(summary["pro"] or 0),
            "new_7d": int(summary["new_7d"] or 0),
            "active_30d": int(summary["active_30d"] or 0),
        },
    }


class UpdateRoleBody(BaseModel):
    role: str


@app.patch("/api/admin/users/{user_id}/role")
async def admin_update_user_role(
    user_id: int,
    body: UpdateRoleBody,
    admin: dict = Depends(require_admin),
):
    role = body.role.strip().lower()
    if role not in ("admin", "member", "retailer"):
        raise HTTPException(status_code=400, detail="Role must be admin, member, or retailer.")
    if user_id == admin["uid"] and role != "admin":
        raise HTTPException(status_code=400, detail="Can't demote yourself.")
    async with get_pool().acquire() as conn:
        result = await conn.execute("UPDATE users SET role = $1 WHERE id = $2", role, user_id)
    if result.endswith(" 0"):
        raise HTTPException(status_code=404, detail="User not found.")
    return {"message": "Role updated.", "id": user_id, "role": role}


class GrantProBody(BaseModel):
    days: int = 30


@app.post("/api/admin/users/{user_id}/grant-pro")
async def admin_grant_pro(
    user_id: int,
    body: GrantProBody,
    admin: dict = Depends(require_admin),
):
    if body.days < 1 or body.days > 36500:
        raise HTTPException(status_code=400, detail="days must be between 1 and 36500.")
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE users
               SET pro_until = GREATEST(COALESCE(pro_until, NOW()), NOW()) + ($1 || ' days')::interval
               WHERE id = $2
               RETURNING pro_until""",
            str(body.days), user_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"message": f"Granted {body.days} days of Pro.", "id": user_id, "pro_until": row["pro_until"].isoformat()}


@app.post("/api/admin/users/{user_id}/revoke-pro")
async def admin_revoke_pro(user_id: int, admin: dict = Depends(require_admin)):
    async with get_pool().acquire() as conn:
        result = await conn.execute("UPDATE users SET pro_until = NULL WHERE id = $1", user_id)
    if result.endswith(" 0"):
        raise HTTPException(status_code=404, detail="User not found.")
    return {"message": "Pro revoked.", "id": user_id}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin: dict = Depends(require_admin)):
    if user_id == admin["uid"]:
        raise HTTPException(status_code=400, detail="Can't delete yourself.")
    async with get_pool().acquire() as conn:
        result = await conn.execute("DELETE FROM users WHERE id = $1", user_id)
    if result.endswith(" 0"):
        raise HTTPException(status_code=404, detail="User not found.")
    return {"message": "User deleted.", "id": user_id}


@app.get("/api/admin/retailers")
async def admin_list_retailers(user: dict = Depends(require_admin)):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT rp.id, rp.retailer_id, rp.state_code, rp.store_name, rp.city,
                      rp.verified, rp.created_at, u.email
               FROM retailer_profiles rp JOIN users u ON u.id=rp.user_id
               ORDER BY rp.created_at DESC"""
        )
    return {
        "retailers": [
            {
                "id": r["id"], "retailer_id": r["retailer_id"], "state_code": r["state_code"],
                "store_name": r["store_name"], "city": r["city"], "verified": r["verified"],
                "created_at": r["created_at"].isoformat(), "email": r["email"],
            }
            for r in rows
        ]
    }


@app.get("/api/games")
async def api_games(
    state: Optional[str] = Query(None, description="Filter by state code (e.g. TX)"),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    min_return: Optional[float] = Query(None, description="Minimum return % (e.g. 60)"),
    sort_by: str = Query("return_pct", description="Sort field"),
    limit: int = Query(500, le=5000),
):
    async with get_pool().acquire() as conn:
        rows = await get_all_games(
            conn,
            state=state,
            min_price=min_price,
            max_price=max_price,
            min_return=min_return,
            sort_by=sort_by,
            limit=limit,
        )

    cols = [
        "id", "state_code", "state_name", "game_id", "name", "price",
        "ev", "return_pct", "overall_odds_one_in",
        "top_prize", "top_prize_remaining",
        "total_tickets", "tickets_remaining",
        "detail_url", "image_url", "scraped_at",
        "prize_pool_remaining", "jackpot_odds_one_in", "ev_approximate",
        "start_date", "top_prize_is_annuity", "top_prize_cash_value",
        "top_prize_annuity_years", "top_prize_annuity_annual",
        "has_second_chance", "second_chance_url",
    ]
    games = []
    today = datetime.date.today()
    for row in rows:
        g = dict(zip(cols, row))
        sd = g.get("start_date")
        tt = g.get("total_tickets")
        tr = g.get("tickets_remaining")
        if sd and tt and tr is not None and tt > tr:
            days = max(1, (today - sd).days)
            g["days_on_sale"] = days
            g["tickets_sold_per_day"] = round((tt - tr) / days)
            if g["tickets_sold_per_day"] > 0:
                g["estimated_days_to_sellout"] = round(tr / g["tickets_sold_per_day"])
        games.append(g)
    return {"games": games, "count": len(games)}


@app.get("/api/games/strategy-stats")
async def api_strategy_stats():
    """Per-game prize-tier aggregations powering EV sub-tab strategies.

    Returns current overall odds (live, based on prizes_remaining + tickets_remaining)
    plus odds at each meaningful prize threshold so the frontend can rank games
    by 'best chance to win $X+' without round-tripping per game.
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT g.id, g.tickets_remaining,
                   SUM(CASE WHEN pt.prizes_remaining > 0 THEN pt.prizes_remaining ELSE 0 END) AS prizes_remaining_total,
                   SUM(CASE WHEN pt.prize_amount >= 50      AND pt.prizes_remaining > 0 THEN pt.prizes_remaining ELSE 0 END) AS p_50,
                   SUM(CASE WHEN pt.prize_amount >= 100     AND pt.prizes_remaining > 0 THEN pt.prizes_remaining ELSE 0 END) AS p_100,
                   SUM(CASE WHEN pt.prize_amount >= 500     AND pt.prizes_remaining > 0 THEN pt.prizes_remaining ELSE 0 END) AS p_500,
                   SUM(CASE WHEN pt.prize_amount >= 1000    AND pt.prizes_remaining > 0 THEN pt.prizes_remaining ELSE 0 END) AS p_1k,
                   SUM(CASE WHEN pt.prize_amount >= 5000    AND pt.prizes_remaining > 0 THEN pt.prizes_remaining ELSE 0 END) AS p_5k,
                   SUM(CASE WHEN pt.prize_amount >= 10000   AND pt.prizes_remaining > 0 THEN pt.prizes_remaining ELSE 0 END) AS p_10k,
                   SUM(CASE WHEN pt.prize_amount >= 100000  AND pt.prizes_remaining > 0 THEN pt.prizes_remaining ELSE 0 END) AS p_100k
            FROM games g
            LEFT JOIN prize_tiers pt ON pt.game_db_id = g.id
            WHERE g.is_active = TRUE
              AND g.ev IS NOT NULL
              AND (g.end_date IS NULL OR g.end_date >= CURRENT_DATE)
              AND g.tickets_remaining IS NOT NULL
              AND g.tickets_remaining > 0
            GROUP BY g.id, g.tickets_remaining
            """
        )

    def odds(prizes, tickets):
        if not prizes or not tickets:
            return None
        return float(tickets) / float(prizes)

    stats = []
    for r in rows:
        tr = r["tickets_remaining"]
        stats.append({
            "id": r["id"],
            "odds_any": odds(r["prizes_remaining_total"], tr),
            "odds_50":   odds(r["p_50"], tr),
            "odds_100":  odds(r["p_100"], tr),
            "odds_500":  odds(r["p_500"], tr),
            "odds_1k":   odds(r["p_1k"], tr),
            "odds_5k":   odds(r["p_5k"], tr),
            "odds_10k":  odds(r["p_10k"], tr),
            "odds_100k": odds(r["p_100k"], tr),
            "prizes_remaining_total": int(r["prizes_remaining_total"] or 0),
            "prizes_1k_plus":  int(r["p_1k"] or 0),
            "prizes_10k_plus": int(r["p_10k"] or 0),
        })
    return {"stats": stats, "count": len(stats)}


@app.get("/api/games/{game_id}")
async def api_game_detail(game_id: int):
    async with get_pool().acquire() as conn:
        rows = await get_game_detail(conn, game_id)

    if not rows:
        raise HTTPException(status_code=404, detail="Game not found")

    game_cols = [
        "id", "state_code", "state_name", "game_id", "name", "price",
        "ev", "return_pct", "overall_odds_one_in",
        "top_prize", "top_prize_remaining",
        "total_tickets", "tickets_remaining",
        "prize_pool_left", "is_active", "detail_url", "image_url", "scraped_at",
        "how_to_play", "ev_approximate",
        "start_date", "top_prize_is_annuity", "top_prize_cash_value",
        "top_prize_annuity_years", "top_prize_annuity_annual",
        "has_second_chance", "second_chance_url",
    ]
    tier_cols = ["prize_amount", "odds_one_in", "prizes_total", "prizes_remaining", "last_claimed_at"]

    game = dict(zip(game_cols, rows[0][:len(game_cols)]))
    tiers = []
    for row in rows:
        tier_vals = row[len(game_cols):]
        if tier_vals[0] is not None:
            tier = dict(zip(tier_cols, tier_vals))
            if tier.get("last_claimed_at"):
                tier["last_claimed_at"] = tier["last_claimed_at"].isoformat()
            tiers.append(tier)

    tiers.sort(key=lambda t: t["prize_amount"] or 0, reverse=True)
    game["prize_tiers"] = tiers

    sd = game.get("start_date")
    tt = game.get("total_tickets")
    tr = game.get("tickets_remaining")
    if sd and tt and tr is not None and tt > tr:
        today = datetime.date.today()
        days = max(1, (today - sd).days)
        game["days_on_sale"] = days
        game["tickets_sold_per_day"] = round((tt - tr) / days)
        if game["tickets_sold_per_day"] > 0:
            game["estimated_days_to_sellout"] = round(tr / game["tickets_sold_per_day"])

    async with get_pool().acquire() as conn:
        sales = await get_weekly_sales(conn, game["state_code"], game["game_id"], limit=26)
    game["weekly_sales"] = [
        {**s, "week_ending": s["week_ending"].isoformat() if s.get("week_ending") else None}
        for s in sales
    ]
    return game


@app.get("/api/games/{game_id}/sales")
async def api_game_sales(game_id: int, weeks: int = Query(52, le=260)):
    async with get_pool().acquire() as conn:
        meta = await conn.fetchrow(
            "SELECT state_code, game_id FROM games WHERE id=$1", game_id,
        )
        if not meta:
            raise HTTPException(status_code=404, detail="Game not found")
        sales = await get_weekly_sales(conn, meta["state_code"], meta["game_id"], limit=weeks)
    return {
        "game_id": game_id,
        "weekly_sales": [
            {**s, "week_ending": s["week_ending"].isoformat() if s.get("week_ending") else None}
            for s in sales
        ],
    }


@app.get("/api/second-chance")
async def api_second_chance(state: Optional[str] = Query(None), upcoming_only: bool = Query(True), limit: int = Query(100, le=500)):
    async with get_pool().acquire() as conn:
        rows = await get_second_chance(conn, state_code=state, upcoming_only=upcoming_only, limit=limit)
    for r in rows:
        if r.get("drawing_date"):
            r["drawing_date"] = r["drawing_date"].isoformat()
    return {"drawings": rows, "count": len(rows)}


@app.get("/api/states")
async def api_states():
    async with get_pool().acquire() as conn:
        rows = await get_states_summary(conn)
    cols = ["state_code", "state_name", "game_count", "last_scraped", "avg_return", "best_return"]
    return {"states": [dict(zip(cols, r)) for r in rows]}


@app.get("/api/status/states")
async def api_status_states():
    from backend.scraper.runner import ALL_SCRAPERS
    all_known = {cls.state_code: cls.state_name for cls in ALL_SCRAPERS}

    from backend.retailer_scrapers.runner import SCRAPERS as RETAILER_STATES
    retailer_state_set = set(RETAILER_STATES)

    async with get_pool().acquire() as conn:
        game_rows = await conn.fetch("""
            SELECT
                state_code,
                COUNT(*) AS games_in_db,
                ROUND(100.0 * COUNT(ev) / COUNT(*), 0) AS ev_pct,
                ROUND(100.0 * COUNT(image_url) / COUNT(*), 0) AS image_pct,
                ROUND(AVG(CASE WHEN ev IS NOT NULL THEN return_pct END)::numeric, 1) AS avg_return,
                ROUND(100.0 * COUNT(CASE WHEN tickets_remaining > 0 THEN 1 END) / NULLIF(COUNT(tickets_remaining),0), 0) AS prizes_pct,
                ROUND(100.0 * SUM(CASE WHEN ev IS NOT NULL AND COALESCE(ev_approximate, FALSE) THEN 1 ELSE 0 END) / NULLIF(COUNT(ev), 0), 0) AS approx_pct
            FROM games WHERE is_active=TRUE
            GROUP BY state_code
        """)
        # Correlated subquery here used to time out as scrape_log grew (~30k rows/mo).
        # Replaced with two indexed CTEs joined once. Needs idx_scrape_log_state_ran.
        log_rows = await conn.fetch("""
            WITH latest_per_state AS (
                SELECT DISTINCT ON (state_code)
                    state_code, success, ran_at, error_msg
                FROM scrape_log
                ORDER BY state_code, ran_at DESC
            ),
            latest_success AS (
                SELECT state_code, MAX(ran_at) AS last_success_at
                FROM scrape_log
                WHERE success = TRUE
                GROUP BY state_code
            )
            SELECT lp.state_code, lp.success, lp.ran_at, lp.error_msg,
                   ls.last_success_at
            FROM latest_per_state lp
            LEFT JOIN latest_success ls USING (state_code)
        """)
        retailer_rows = await conn.fetch(
            "SELECT state_code, last_scraped_at FROM retailer_scrape_log"
        )
        winner_rows = await conn.fetch("""
            SELECT state_code,
                   COUNT(*) AS wins,
                   MAX(claim_date) AS latest,
                   SUM(CASE WHEN retailer_lat IS NOT NULL THEN 1 ELSE 0 END) AS geocoded,
                   SUM(CASE WHEN retailer_name IS NOT NULL THEN 1 ELSE 0 END) AS with_retailer,
                   COUNT(*) FILTER (WHERE claim_date > CURRENT_DATE - INTERVAL '60 days') AS wins_60d
            FROM reported_wins
            GROUP BY state_code
        """)

    games_by_state = {r["state_code"]: r for r in game_rows}
    log_by_state = {r["state_code"]: r for r in log_rows}
    retailer_by_state = {r["state_code"]: r for r in retailer_rows}
    winners_by_state = {r["state_code"]: r for r in winner_rows}

    from backend.scraper.winners.runner import ALL_WINNERS_SCRAPERS
    winners_scraper_set = {cls.state_code for cls in ALL_WINNERS_SCRAPERS}

    # Some states (notably IL) flap between OK and 403/timeout every run because
    # the upstream lottery site's Cloudflare actively challenges us. If the most
    # recent run failed but a successful run landed within this window, treat
    # the state as healthy — the DB data is still fresh.
    FLAP_GRACE = datetime.timedelta(hours=2)
    now = datetime.datetime.now(datetime.timezone.utc)

    states = []
    for state_code in sorted(all_known, key=lambda c: all_known[c]):
        state_name = all_known[state_code]
        log = log_by_state.get(state_code)
        g = games_by_state.get(state_code)
        games = int(g["games_in_db"]) if g else 0
        if log:
            if log["success"]:
                status = "ok"
                error_msg = None
            elif log["last_success_at"] and (now - log["last_success_at"]) < FLAP_GRACE:
                status = "ok"
                error_msg = None
            else:
                status = "error"
                error_msg = log["error_msg"]
        elif games:
            status = "warn"
            error_msg = None
        else:
            status = "never"
            error_msg = None
        ret = retailer_by_state.get(state_code)
        w = winners_by_state.get(state_code)
        states.append({
            "state_code": state_code,
            "state_name": state_name,
            "games_in_db": games,
            "last_scrape_at": log["ran_at"].isoformat() if log else None,
            "last_scrape_error": error_msg,
            "status": status,
            "ev_pct": int(g["ev_pct"] or 0) if g else 0,
            "approx_pct": int(g["approx_pct"] or 0) if g else 0,
            "image_pct": int(g["image_pct"] or 0) if g else 0,
            "avg_return": float(g["avg_return"] or 0) if g else 0,
            "prizes_pct": int(g["prizes_pct"] or 0) if g else 0,
            "has_retailer_scraper": state_code in retailer_state_set,
            "retailer_last_scraped": ret["last_scraped_at"].isoformat() if ret else None,
            "has_winners_scraper": state_code in winners_scraper_set,
            "winners_count": int(w["wins"]) if w else 0,
            "winners_recent_count": int(w["wins_60d"]) if w else 0,
            "winners_latest": w["latest"].isoformat() if (w and w["latest"]) else None,
            "winners_geocoded_pct": int(round(100 * w["geocoded"] / w["wins"])) if (w and w["wins"]) else 0,
            "winners_has_retailer": bool(w and w["with_retailer"] and w["with_retailer"] > 0),
        })

    return {
        "states": states,
        "scraper_running": scrape_status["running"],
        "current_state": scrape_status.get("current_state"),
        "last_run": scrape_status.get("last_run"),
    }


@app.get("/api/status")
async def api_status(
    limit: int = Query(20, ge=1, le=500),
    state: Optional[str] = Query(None, description="Filter scrape_log to one state code"),
):
    async with get_pool().acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM games WHERE is_active=TRUE")
        states = await conn.fetchval("SELECT COUNT(DISTINCT state_code) FROM games WHERE is_active=TRUE")
        if state:
            rows = await conn.fetch(
                "SELECT ran_at, state_code, success, games_scraped, error_msg "
                "FROM scrape_log WHERE state_code=$1 ORDER BY ran_at DESC LIMIT $2",
                state.upper(), limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT ran_at, state_code, success, games_scraped, error_msg "
                "FROM scrape_log ORDER BY ran_at DESC LIMIT $1",
                limit,
            )
        log = [dict(zip(["ran_at", "state_code", "success", "games_scraped", "error_msg"], r)) for r in rows]
        db_last_run = await conn.fetchval("SELECT MAX(ran_at) FROM scrape_log")
    last_run = scrape_status["last_run"] or (db_last_run.isoformat() if db_last_run else None)
    return {
        "total_games": total,
        "states_covered": states,
        "scraper_running": scrape_status["running"],
        "last_run": last_run,
        "recent_log": log,
    }


@app.post("/api/scrape")
async def api_trigger_scrape(
    background_tasks: BackgroundTasks,
    state: Optional[str] = Query(None, description="Scrape a single state (e.g. TX), or all if omitted"),
    user: dict = Depends(require_admin),
):
    if scrape_status["running"]:
        return {"message": "Scrape already in progress", "running": True}

    async def _run():
        scrape_status["running"] = True
        scrape_status["current_state"] = None
        try:
            from backend.scraper.runner import run_all
            def _on_state(code, name):
                scrape_status["current_state"] = {"code": code, "name": name}
            results = await run_all(state_filter=state, on_state=_on_state)
            scrape_status["last_results"] = results
            import datetime
            scrape_status["last_run"] = datetime.datetime.utcnow().isoformat()
            clear_games_cache()
        finally:
            scrape_status["running"] = False
            scrape_status["current_state"] = None

    background_tasks.add_task(_run)
    return {"message": f"Scrape started for {'all states' if not state else state}", "running": True}


retailer_scrape_status: dict = {}  # state_code -> {"running": bool, "last_result": dict|None}


@app.post("/api/admin/scrape/retailers/{state_code}")
async def api_trigger_retailer_scrape(
    state_code: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_admin),
):
    code = state_code.upper()
    from backend.retailer_scrapers.runner import SCRAPERS
    if code not in SCRAPERS:
        raise HTTPException(status_code=404, detail=f"No retailer scraper for {code}")
    if retailer_scrape_status.get(code, {}).get("running"):
        return {"message": f"{code} retailer scrape already running", "running": True}

    async def _run():
        retailer_scrape_status[code] = {"running": True, "last_result": None}
        try:
            from backend.retailer_scrapers.runner import _run_state
            result = await _run_state(code)
            retailer_scrape_status[code] = {"running": False, "last_result": result}
            logger.info("Manual retailer scrape done: %s — %d retailers", code, result.get("count", 0))
        except Exception as e:
            logger.error("Manual retailer scrape failed: %s — %s", code, e)
            retailer_scrape_status[code] = {"running": False, "last_result": {"error": str(e)}}

    background_tasks.add_task(_run)
    return {"message": f"Retailer scrape started for {code}", "running": True}


@app.get("/api/admin/scrape/retailers/{state_code}/status")
async def api_retailer_scrape_status(state_code: str, user: dict = Depends(require_admin)):
    code = state_code.upper()
    return retailer_scrape_status.get(code, {"running": False, "last_result": None})


@app.post("/api/scrape/cancel")
async def api_cancel_scrape():
    if not scrape_status["running"]:
        return {"message": "No scrape running"}
    from backend.scraper.runner import request_cancel
    request_cancel()
    return {"message": "Cancel requested — active scrapers will finish, queued ones will be skipped"}


@app.get("/ma-heatmap", include_in_schema=False)
async def ma_heatmap():
    path = os.path.join(os.path.dirname(__file__), "..", "ma_heatmap.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Heatmap not generated yet. Run heatmap_generator.py first.")
    return FileResponse(path, media_type="text/html")


_prize_claims_cache: dict = {}  # key -> (timestamp, payload)
_PRIZE_CLAIMS_TTL = 120  # seconds

@app.get("/api/prize-claims")
async def api_prize_claims(
    days: int = Query(7, le=7300),
    min_prize: float = Query(0, ge=0),
    limit: int = Query(100000, ge=1, le=200000),
):
    cache_key = (days, min_prize, limit)
    cached = _prize_claims_cache.get(cache_key)
    if cached and (datetime.datetime.utcnow().timestamp() - cached[0]) < _PRIZE_CLAIMS_TTL:
        return cached[1]
    async with get_pool().acquire() as conn:
        claims = await get_recent_prize_claims(conn, days=days, min_prize=min_prize, limit=limit)
    for c in claims:
        if c.get("detected_at"):
            c["detected_at"] = c["detected_at"].isoformat()
    result = {"claims": claims, "count": len(claims), "fetched_at": datetime.datetime.utcnow().isoformat() + "Z"}
    _prize_claims_cache[cache_key] = (datetime.datetime.utcnow().timestamp(), result)
    return result


_reported_wins_cache: dict = {}
_REPORTED_WINS_TTL = 120


@app.get("/api/reported-wins")
async def api_reported_wins(
    days: int = Query(30, le=7300),
    min_prize: float = Query(10000, ge=0),
    state: Optional[str] = Query(None, description="2-letter state code filter"),
    has_location: bool = Query(False, description="Only wins with lat/lng"),
    limit: int = Query(1000),
):
    cache_key = (days, min_prize, state, has_location, limit)
    cached = _reported_wins_cache.get(cache_key)
    if cached and (datetime.datetime.utcnow().timestamp() - cached[0]) < _REPORTED_WINS_TTL:
        return cached[1]
    async with get_pool().acquire() as conn:
        wins = await get_reported_wins(conn, days=days, min_prize=min_prize,
                                        state=state, has_location=has_location,
                                        limit=limit)
    for w in wins:
        if w.get("claim_date"):
            w["claim_date"] = w["claim_date"].isoformat()
        if w.get("scraped_at"):
            w["scraped_at"] = w["scraped_at"].isoformat()
    result = {
        "wins": wins,
        "count": len(wins),
        "states_with_data": sorted({w["state_code"] for w in wins}),
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    _reported_wins_cache[cache_key] = (datetime.datetime.utcnow().timestamp(), result)
    return result


@app.get("/api/reported-wins/map")
async def api_reported_wins_map(
    days: int = Query(1095, le=7300, description="Time window in days (default 3 years)"),
    min_prize: float = Query(10000, ge=0),
):
    """Aggregated location groups for the Big Wins map. Bypasses the row cap that
    plagues /api/reported-wins — for the map we only need one point per location,
    so we aggregate server-side. Returns every geocoded location, not a top-N
    slice. Filtering by state and game happens client-side off this dataset."""
    cache_key = ("map", days, min_prize)
    cached = _reported_wins_cache.get(cache_key)
    if cached and (datetime.datetime.utcnow().timestamp() - cached[0]) < _REPORTED_WINS_TTL:
        return cached[1]

    async with get_pool().acquire() as conn:
        rows = await get_reported_wins_for_map(conn, days=days, min_prize=min_prize)

    groups: dict = {}
    states_set: set = set()
    game_counts: dict = {}
    total_wins = 0
    total_prize = 0.0
    TOP_WINS_PER_GROUP = 12

    for r in rows:
        state_code = r["state_code"]
        retailer_name = r["retailer_name"]
        retailer_lat = r["retailer_lat"]
        retailer_lng = r["retailer_lng"]
        prize = float(r["prize_amount"] or 0)
        game_name = (r["source_game_name"] or "").strip() or "(unknown)"
        claim_date = r["claim_date"]
        winner_city = r["winner_city"]

        states_set.add(state_code)
        game_counts[game_name] = game_counts.get(game_name, 0) + 1
        total_wins += 1
        total_prize += prize

        is_home = retailer_name is None
        if is_home:
            key = ("home", state_code, (winner_city or "").lower())
        else:
            key = ("ret", round(retailer_lat, 5), round(retailer_lng, 5))

        g = groups.get(key)
        if g is None:
            g = {
                "lat": retailer_lat,
                "lng": retailer_lng,
                "state_code": state_code,
                "is_home": is_home,
                "retailer_name": retailer_name,
                "retailer_city": r["retailer_city"],
                "winner_city": winner_city,
                "win_count": 0,
                "total_prize": 0.0,
                "max_prize": 0.0,
                "last_claim_date": None,
                "games": {},
                "top_wins": [],
            }
            groups[key] = g

        g["win_count"] += 1
        g["total_prize"] += prize
        if prize > g["max_prize"]:
            g["max_prize"] = prize
        if claim_date and (g["last_claim_date"] is None or claim_date > g["last_claim_date"]):
            g["last_claim_date"] = claim_date
        g["games"][game_name] = g["games"].get(game_name, 0) + 1
        if len(g["top_wins"]) < TOP_WINS_PER_GROUP:
            g["top_wins"].append({
                "prize_amount": prize,
                "source_game_name": r["source_game_name"],
                "claim_date": claim_date.isoformat() if claim_date else None,
                "game_db_id": r["game_db_id"],
            })

    for g in groups.values():
        if g["last_claim_date"]:
            g["last_claim_date"] = g["last_claim_date"].isoformat()

    from backend.scraper.winners.runner import WINNERS_FEED_STATES

    result = {
        "groups": list(groups.values()),
        "total_wins": total_wins,
        "total_prize": total_prize,
        "total_locations": len(groups),
        "states_with_data": sorted(states_set),
        "states_with_feeds": WINNERS_FEED_STATES,
        "game_counts": [
            {"name": n, "count": c}
            for n, c in sorted(game_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    _reported_wins_cache[cache_key] = (datetime.datetime.utcnow().timestamp(), result)
    return result


@app.post("/api/admin/scrape-winners")
async def api_scrape_winners(
    background: BackgroundTasks,
    state: Optional[str] = Query(None),
    days: int = Query(14, le=7300),
    user = Depends(require_admin),
):
    async def _run():
        from backend.scraper.winners.runner import run_all
        results = await run_all(state_filter=state, days=days)
        logger.info("winners scrape complete: %s", results)
        _reported_wins_cache.clear()
    background.add_task(_run)
    return {"started": True, "state": state, "days": days}


@app.get("/api/az/retailers")
async def api_az_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    from backend.az_scorer import load_and_score_async
    async with get_pool().acquire() as conn:
        retailers = await load_and_score_async(conn)
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/ma/retailers")
async def api_ma_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    from backend.ma_scorer import load_and_score_async
    async with get_pool().acquire() as conn:
        retailers = await load_and_score_async(conn)
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/ri/retailers")
async def api_ri_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    from backend.ri_scorer import load_and_score_async
    async with get_pool().acquire() as conn:
        retailers = await load_and_score_async(conn)
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/ga/retailers")
async def api_ga_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    from backend.ga_scorer import load_and_score_async
    async with get_pool().acquire() as conn:
        retailers = await load_and_score_async(conn)
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/fl/retailers")
async def api_fl_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    from backend.fl_scorer import load_and_score_async
    async with get_pool().acquire() as conn:
        retailers = await load_and_score_async(conn)
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/ny/retailers")
async def api_ny_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    from backend.ny_scorer import load_and_score_async
    async with get_pool().acquire() as conn:
        retailers = await load_and_score_async(conn)
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/dc/retailers")
async def api_dc_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, address, city, zip_code, phone, latitude, longitude
               FROM state_retailers WHERE state_code='DC' AND is_active=TRUE
               ORDER BY city, name"""
        )
    retailers = [
        {
            "id":        str(r["id"]),
            "name":      r["name"] or "",
            "address":   r["address"] or "",
            "city":      r["city"] or "",
            "zipCode":   r["zip_code"] or "",
            "phone":     r["phone"] or "",
            "latitude":  r["latitude"],
            "longitude": r["longitude"],
        }
        for r in rows
    ]
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/va/retailers")
async def api_va_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, address, city, zip_code, phone, latitude, longitude
               FROM state_retailers WHERE state_code='VA' AND is_active=TRUE
               ORDER BY city, name"""
        )
    retailers = [
        {
            "id":        str(r["id"]),
            "name":      r["name"] or "",
            "address":   r["address"] or "",
            "city":      r["city"] or "",
            "zipCode":   r["zip_code"] or "",
            "phone":     r["phone"] or "",
            "latitude":  r["latitude"],
            "longitude": r["longitude"],
        }
        for r in rows
    ]
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/vt/retailers")
async def api_vt_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, address, city, zip_code, phone, latitude, longitude
               FROM state_retailers WHERE state_code='VT' AND is_active=TRUE
               ORDER BY city, name"""
        )
    retailers = [
        {
            "id":        str(r["id"]),
            "name":      r["name"] or "",
            "address":   r["address"] or "",
            "city":      r["city"] or "",
            "zipCode":   r["zip_code"] or "",
            "phone":     r["phone"] or "",
            "latitude":  r["latitude"],
            "longitude": r["longitude"],
        }
        for r in rows
    ]
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/me/retailers")
async def api_me_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, address, city, zip_code, phone, latitude, longitude
               FROM state_retailers WHERE state_code='ME' AND is_active=TRUE
               ORDER BY city, name"""
        )
    retailers = [
        {
            "id":        str(r["id"]),
            "name":      r["name"] or "",
            "address":   r["address"] or "",
            "city":      r["city"] or "",
            "zipCode":   r["zip_code"] or "",
            "phone":     r["phone"] or "",
            "latitude":  r["latitude"],
            "longitude": r["longitude"],
        }
        for r in rows
    ]
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/state/{state_code}/retailers")
async def api_state_retailers(
    state_code: str,
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=30000),
):
    """Generic retailer endpoint backed by the state_retailers table.
    Used by 'Live' Chase states that don't have a custom scorer (CO, CT, ME, MI,
    NJ, OR, SC, WA, etc.)."""
    code = state_code.upper()
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, address, city, zip_code, phone, latitude, longitude
               FROM state_retailers WHERE state_code=$1 AND is_active=TRUE
               ORDER BY city, name""",
            code,
        )
    retailers = [
        {
            "id":        str(r["id"]),
            "name":      r["name"] or "",
            "address":   r["address"] or "",
            "city":      r["city"] or "",
            "zipCode":   r["zip_code"] or "",
            "phone":     r["phone"] or "",
            "latitude":  r["latitude"],
            "longitude": r["longitude"],
        }
        for r in rows
    ]
    if search:
        q = search.lower()
        retailers = [r for r in retailers if q in r["name"].lower() or q in r["city"].lower()]
    return {"retailers": retailers[:limit], "total": len(retailers)}


@app.get("/api/scrape/status")
async def api_scrape_status():
    return {
        "running": scrape_status["running"],
        "last_run": scrape_status["last_run"],
        "last_results": scrape_status["last_results"],
    }


# ── Community inventory reports ───────────────────────────────────────────────

class InventoryReportBody(BaseModel):
    retailer_id: str
    retailer_name: str = ""
    retailer_city: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    game_name: str = ""
    game_price: Optional[float] = None
    has_stock: bool = False
    notes: Optional[str] = None
    reported_at: Optional[str] = None
    # Reporter's device location at submission time (separate from the retailer's
    # known lat/lng above). Required for bounty geo-verification — without these
    # the report is still accepted but doesn't count toward bounty progress.
    reporter_lat: Optional[float] = None
    reporter_lng: Optional[float] = None
    # Bounty session id from the mobile client groups a batch of submissions
    # made during a single display-scan session. Empty for non-bounty submits.
    bounty_session: Optional[str] = None


@app.post("/api/inventory/report")
async def submit_inventory_report(
    body: InventoryReportBody,
    request: Request,
    user: dict = Depends(require_member),
):
    reporter_ip = request.client.host if request.client else None
    is_admin = user.get("role") == "admin"
    async with get_pool().acquire() as conn:
        if reporter_ip and not is_admin:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM inventory_reports WHERE reporter_ip=$1 AND reported_at > NOW() - INTERVAL '1 hour'",
                reporter_ip,
            )
            if count >= 300:
                raise HTTPException(status_code=429, detail="Too many reports. Try again in an hour.")
        await add_inventory_report(
            conn,
            retailer_id=body.retailer_id,
            retailer_name=body.retailer_name or None,
            retailer_city=body.retailer_city or None,
            lat=body.lat,
            lng=body.lng,
            game_name=body.game_name or None,
            game_price=body.game_price,
            has_stock=body.has_stock,
            source="admin" if is_admin else "community",
            reporter_ip=reporter_ip,
            reporter_username=user.get("username"),
            notes=body.notes or None,
            reported_at=body.reported_at or None,
            reporter_lat=body.reporter_lat,
            reporter_lng=body.reporter_lng,
            bounty_session=body.bounty_session,
        )
    return {"message": "Report submitted!", "reported_at": datetime.datetime.utcnow().isoformat()}


@app.get("/api/inventory/reports")
async def get_inventory_reports(
    limit: int = Query(200, le=500),
    retailer_id: Optional[str] = Query(None),
    game_name: Optional[str] = Query(None),
    user: dict = Depends(require_member),
):
    async with get_pool().acquire() as conn:
        reports = await get_recent_inventory_reports(
            conn, limit=limit, retailer_id=retailer_id, game_name=game_name
        )
    return {"reports": reports, "count": len(reports)}


@app.get("/api/inventory/game-counts")
async def get_inventory_game_counts(user: dict = Depends(require_member)):
    """Members-only: returns count of distinct retailers per game whose latest
    report says the game is in stock. Games with zero in-stock retailers are omitted."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            WITH latest AS (
                SELECT DISTINCT ON (retailer_id, LOWER(game_name))
                    LOWER(game_name) AS gname, has_stock
                FROM inventory_reports
                WHERE game_name IS NOT NULL AND retailer_id IS NOT NULL
                ORDER BY retailer_id, LOWER(game_name), reported_at DESC
            )
            SELECT gname, COUNT(*) FROM latest WHERE has_stock = TRUE GROUP BY gname
        """)
    return {"counts": {r[0]: r[1] for r in rows}}


@app.get("/api/inventory/retailer-counts")
async def get_inventory_retailer_counts(user: dict = Depends(require_member)):
    """Members-only: returns report counts per retailer ID."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT retailer_id, COUNT(*) FROM inventory_reports WHERE retailer_id IS NOT NULL GROUP BY retailer_id"
        )
    return {"counts": {r[0]: r[1] for r in rows}}


@app.get("/api/inventory/retailer-latest")
async def get_retailer_latest_status(
    game_name: Optional[str] = Query(None),
    user: dict = Depends(require_member),
):
    """Members-only: latest inventory status per retailer.
    Pass game_name to filter to a specific game."""
    async with get_pool().acquire() as conn:
        if game_name:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (retailer_id)
                    retailer_id, has_stock, reported_at,
                    game_name, game_price, reporter_username
                FROM inventory_reports
                WHERE retailer_id IS NOT NULL AND LOWER(game_name) = LOWER($1)
                ORDER BY retailer_id, reported_at DESC
            """, game_name)
        else:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (retailer_id)
                    retailer_id, has_stock, reported_at,
                    game_name, game_price, reporter_username
                FROM inventory_reports
                WHERE retailer_id IS NOT NULL
                ORDER BY retailer_id, reported_at DESC
            """)
    return {
        "statuses": {
            r["retailer_id"]: {
                "has_stock": r["has_stock"],
                "reported_at": r["reported_at"],
                "game_name": r["game_name"],
                "game_price": float(r["game_price"]) if r["game_price"] is not None else None,
                "reporter_username": r["reporter_username"],
            }
            for r in rows
        }
    }


# ── Bounty: fresh-data rewards for hunter contributions ───────────────────────
#
# When a retailer's most recent inventory report is older than BOUNTY_STALE_DAYS
# (or there are no reports at all), the location is "bounty eligible". A member
# who runs a verified display-scan session there — at least BOUNTY_PHOTO_MIN
# geo-verified photos that collectively detect BOUNTY_DISTINCT_GAMES_MIN unique
# catalog games — can claim BOUNTY_REWARD_DAYS of free Pro. Per-user-per-store
# cooldown prevents grinding the same location repeatedly.
#
# All thresholds live here so they can be tuned without touching client code.

BOUNTY_STALE_DAYS = 14
BOUNTY_PHOTO_MIN = 5
BOUNTY_DISTINCT_GAMES_MIN = 20
BOUNTY_REWARD_DAYS = 30
BOUNTY_GEO_RADIUS_M = 200  # device must be within this many meters of the retailer
BOUNTY_USER_COOLDOWN_DAYS = 60  # same user can't re-claim the same store this often
BOUNTY_SESSION_WINDOW_HOURS = 24  # qualifying reports must be within this window


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters between two lat/lng points."""
    import math
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _retailer_lookup(conn, retailer_id: str):
    """Return (lat, lng, state_code, name) for a retailer, or (None, None, None, None).
    Searches state_retailers (the scraped roster) first, falling back to the most
    recent coordinates we've seen in inventory_reports."""
    row = await conn.fetchrow(
        "SELECT latitude, longitude, state_code, name FROM state_retailers WHERE external_id = $1 LIMIT 1",
        retailer_id,
    )
    if row and row["latitude"] is not None and row["longitude"] is not None:
        return float(row["latitude"]), float(row["longitude"]), row["state_code"], row["name"]
    row = await conn.fetchrow(
        """SELECT lat, lng, retailer_name FROM inventory_reports
           WHERE retailer_id = $1 AND lat IS NOT NULL AND lng IS NOT NULL
           ORDER BY reported_at DESC LIMIT 1""",
        retailer_id,
    )
    if row:
        return float(row["lat"]), float(row["lng"]), None, row["retailer_name"]
    return None, None, None, None


@app.get("/api/bounty/status/{retailer_id}")
async def get_bounty_status(retailer_id: str, user: dict = Depends(require_member)):
    """Whether this retailer is currently bounty-eligible and how much progress the
    calling user has made toward claiming. Driven by the same data the inventory
    map already reads — no separate bounty queue to maintain."""
    user_id = user["uid"]
    username = user.get("username")
    async with get_pool().acquire() as conn:
        # Eligibility: the location's most-recent inventory report is older than
        # the stale threshold, or there are no reports at all.
        latest_row = await conn.fetchrow(
            "SELECT MAX(reported_at) AS latest FROM inventory_reports WHERE retailer_id = $1",
            retailer_id,
        )
        latest = latest_row["latest"] if latest_row else None
        now = datetime.datetime.now(datetime.timezone.utc)
        stale_cutoff = now - datetime.timedelta(days=BOUNTY_STALE_DAYS)
        is_stale = latest is None or latest < stale_cutoff
        days_since = None
        if latest is not None:
            days_since = (now - latest).total_seconds() / 86400.0

        # Cooldown: did this user already claim this store recently?
        cooldown_cutoff = now - datetime.timedelta(days=BOUNTY_USER_COOLDOWN_DAYS)
        recent_claim = await conn.fetchrow(
            """SELECT claimed_at FROM bounty_claims
               WHERE user_id = $1 AND retailer_id = $2 AND claimed_at > $3
               ORDER BY claimed_at DESC LIMIT 1""",
            user_id, retailer_id, cooldown_cutoff,
        )

        eligible = is_stale and recent_claim is None
        reason = (
            "claimed_recently" if recent_claim is not None
            else "fresh_data_exists" if not is_stale
            else "stale" if latest is not None
            else "never_reported"
        )

        # Progress for the calling user: count qualifying submissions in the
        # session window. "Qualifying" = report came in within BOUNTY_SESSION_WINDOW_HOURS,
        # for this retailer, by this user. Geo verification happens at claim time
        # using the actual retailer coords + report-row geo.
        window_cutoff = now - datetime.timedelta(hours=BOUNTY_SESSION_WINDOW_HOURS)
        if username:
            progress_row = await conn.fetchrow(
                """SELECT COUNT(*) AS reports,
                          COUNT(DISTINCT LOWER(game_name)) AS games
                   FROM inventory_reports
                   WHERE retailer_id = $1
                     AND reporter_username = $2
                     AND reported_at > $3
                     AND bounty_session IS NOT NULL""",
                retailer_id, username, window_cutoff,
            )
            photos_submitted = int(progress_row["reports"] or 0)
            distinct_games = int(progress_row["games"] or 0)
        else:
            photos_submitted = 0
            distinct_games = 0

        can_claim = (
            eligible
            and photos_submitted >= BOUNTY_PHOTO_MIN
            and distinct_games >= BOUNTY_DISTINCT_GAMES_MIN
        )

    return {
        "eligible": eligible,
        "reason": reason,
        "stale_days": days_since,
        "latest_report_at": latest.isoformat() if latest else None,
        "requirements": {
            "photos_min": BOUNTY_PHOTO_MIN,
            "distinct_games_min": BOUNTY_DISTINCT_GAMES_MIN,
            "geo_radius_m": BOUNTY_GEO_RADIUS_M,
            "stale_days": BOUNTY_STALE_DAYS,
            "session_window_hours": BOUNTY_SESSION_WINDOW_HOURS,
        },
        "progress": {
            "photos_submitted": photos_submitted,
            "distinct_games": distinct_games,
        },
        "reward_days": BOUNTY_REWARD_DAYS,
        "can_claim": can_claim,
    }


class BountyClaimBody(BaseModel):
    retailer_id: str
    session_id: Optional[str] = None


@app.post("/api/bounty/claim")
async def claim_bounty(body: BountyClaimBody, user: dict = Depends(require_member)):
    """Verify the user's recent submissions for this store meet the bounty bar,
    grant Pro days, record the claim. Idempotency: a claim within the cooldown
    window for the same (user, retailer) is rejected."""
    user_id = user["uid"]
    username = user.get("username")
    if not username:
        raise HTTPException(status_code=400, detail="Username required to claim bounties.")

    async with get_pool().acquire() as conn:
        # Re-check eligibility server-side. Don't trust the client.
        retailer_lat, retailer_lng, state_code, _name = await _retailer_lookup(conn, body.retailer_id)
        if retailer_lat is None or retailer_lng is None:
            raise HTTPException(status_code=400, detail="Retailer location unknown — cannot verify bounty.")

        now = datetime.datetime.now(datetime.timezone.utc)

        # Cooldown check first — fail fast.
        cooldown_cutoff = now - datetime.timedelta(days=BOUNTY_USER_COOLDOWN_DAYS)
        recent = await conn.fetchrow(
            """SELECT claimed_at FROM bounty_claims
               WHERE user_id = $1 AND retailer_id = $2 AND claimed_at > $3""",
            user_id, body.retailer_id, cooldown_cutoff,
        )
        if recent:
            raise HTTPException(
                status_code=409,
                detail=f"You already claimed a bounty at this store within the last {BOUNTY_USER_COOLDOWN_DAYS} days.",
            )

        # Staleness: only stale stores award bounties. (This also blocks the case
        # where someone else just submitted, freshening the store, between the
        # status check and the claim.)
        latest_row = await conn.fetchrow(
            """SELECT MAX(reported_at) AS latest FROM inventory_reports
               WHERE retailer_id = $1 AND reporter_username != $2""",
            body.retailer_id, username,
        )
        other_latest = latest_row["latest"] if latest_row else None
        stale_cutoff = now - datetime.timedelta(days=BOUNTY_STALE_DAYS)
        if other_latest is not None and other_latest >= stale_cutoff:
            raise HTTPException(
                status_code=409,
                detail="Another hunter already refreshed this store. Bounty no longer available.",
            )

        # Pull qualifying reports. Constrain to this session_id if provided —
        # tighter anti-abuse — else fall back to all bounty-tagged reports in
        # the session window.
        window_cutoff = now - datetime.timedelta(hours=BOUNTY_SESSION_WINDOW_HOURS)
        if body.session_id:
            rows = await conn.fetch(
                """SELECT game_name, reporter_lat, reporter_lng
                   FROM inventory_reports
                   WHERE retailer_id = $1 AND reporter_username = $2
                     AND reported_at > $3
                     AND bounty_session = $4""",
                body.retailer_id, username, window_cutoff, body.session_id,
            )
        else:
            rows = await conn.fetch(
                """SELECT game_name, reporter_lat, reporter_lng
                   FROM inventory_reports
                   WHERE retailer_id = $1 AND reporter_username = $2
                     AND reported_at > $3
                     AND bounty_session IS NOT NULL""",
                body.retailer_id, username, window_cutoff,
            )

        # Geo-verify each row: reporter must have been within radius. Drop rows
        # without geo (no geo = no credit toward bounty, but the inventory data
        # still stands).
        verified_games = set()
        verified_count = 0
        for r in rows:
            r_lat = r["reporter_lat"]
            r_lng = r["reporter_lng"]
            if r_lat is None or r_lng is None:
                continue
            try:
                dist = _haversine_m(float(r_lat), float(r_lng), retailer_lat, retailer_lng)
            except (TypeError, ValueError):
                continue
            if dist > BOUNTY_GEO_RADIUS_M:
                continue
            verified_count += 1
            if r["game_name"]:
                verified_games.add(r["game_name"].lower())

        if verified_count < BOUNTY_PHOTO_MIN or len(verified_games) < BOUNTY_DISTINCT_GAMES_MIN:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Need {BOUNTY_PHOTO_MIN}+ geo-verified reports covering "
                    f"{BOUNTY_DISTINCT_GAMES_MIN}+ distinct games. "
                    f"You have {verified_count} verified, {len(verified_games)} games."
                ),
            )

        # Grant Pro days — same UPDATE shape as the admin grant. GREATEST ensures
        # we extend from now if pro_until is in the past, or from pro_until if
        # it's in the future (so we don't shorten an active sub).
        granted = await conn.fetchrow(
            """UPDATE users
               SET pro_until = GREATEST(COALESCE(pro_until, NOW()), NOW()) + ($1 || ' days')::interval
               WHERE id = $2
               RETURNING pro_until""",
            str(BOUNTY_REWARD_DAYS), user_id,
        )
        if not granted:
            raise HTTPException(status_code=404, detail="User not found.")

        await conn.execute(
            """INSERT INTO bounty_claims
               (user_id, retailer_id, state_code, granted_days, photos_count, distinct_games, session_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            user_id, body.retailer_id, state_code, BOUNTY_REWARD_DAYS,
            verified_count, len(verified_games), body.session_id,
        )

    return {
        "granted_days": BOUNTY_REWARD_DAYS,
        "pro_until": granted["pro_until"].isoformat(),
        "verified_photos": verified_count,
        "distinct_games": len(verified_games),
    }


# ── Admin: inventory report management ────────────────────────────────────────

class AdminInventoryPatchBody(BaseModel):
    retailer_name: Optional[str] = None
    retailer_city: Optional[str] = None
    game_name: Optional[str] = None
    game_price: Optional[float] = None
    has_stock: Optional[bool] = None
    notes: Optional[str] = None


@app.get("/api/admin/inventory")
async def admin_list_inventory_reports(
    limit: int = Query(200, le=1000),
    search: Optional[str] = Query(None),
    retailer_id: Optional[str] = Query(None),
    user: dict = Depends(require_admin),
):
    conditions = []
    params = []
    if retailer_id:
        params.append(retailer_id)
        conditions.append(f"retailer_id = ${len(params)}")
    if search:
        params.append(f"%{search}%")
        idx = len(params)
        conditions.append(
            f"(game_name ILIKE ${idx} OR retailer_name ILIKE ${idx} "
            f"OR retailer_city ILIKE ${idx} OR reporter_username ILIKE ${idx})"
        )
    params.append(limit)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT id, retailer_id, retailer_name, retailer_city, lat, lng,
                   game_name, game_price, has_stock, source,
                   reporter_username, reporter_ip, notes, reported_at
            FROM inventory_reports
            {where}
            ORDER BY reported_at DESC
            LIMIT ${len(params)}
        """, *params)
    return {
        "reports": [dict(r) for r in rows],
        "count": len(rows),
    }


@app.patch("/api/admin/inventory/{report_id}")
async def admin_update_inventory_report(
    report_id: int,
    body: AdminInventoryPatchBody,
    user: dict = Depends(require_admin),
):
    fields = body.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update.")
    set_parts = []
    params = []
    for col, val in fields.items():
        params.append(val)
        set_parts.append(f"{col} = ${len(params)}")
    params.append(report_id)
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            f"UPDATE inventory_reports SET {', '.join(set_parts)} WHERE id = ${len(params)}",
            *params,
        )
    if result.endswith(" 0"):
        raise HTTPException(status_code=404, detail="Inventory report not found.")
    return {"message": "Inventory report updated.", "id": report_id, "updated": fields}


@app.delete("/api/admin/inventory/{report_id}")
async def admin_delete_inventory_report(
    report_id: int,
    user: dict = Depends(require_admin),
):
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM inventory_reports WHERE id = $1", report_id
        )
    if result.endswith(" 0"):
        raise HTTPException(status_code=404, detail="Inventory report not found.")
    return {"message": "Inventory report deleted.", "id": report_id}
