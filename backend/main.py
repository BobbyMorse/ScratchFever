"""
ScratchFever API — FastAPI backend.
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
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.database import (
    init_db, get_pool, get_all_games, get_game_detail, get_states_summary,
    add_inventory_report, get_recent_inventory_reports, get_recent_prize_claims,
)
from backend.caller.db import init_caller_db
from backend.caller.webhook import router as caller_webhook_router
from backend.caller.api import router as caller_api_router, set_runner
from backend.caller.runner import CallRunner
from backend.users import init_users_db, seed_admin, require_member
from backend.auth_api import router as auth_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
scrape_status = {"running": False, "last_run": None, "last_results": []}


async def scheduled_scrape():
    if scrape_status["running"]:
        logger.info("Scrape already running, skipping scheduled run")
        return
    scrape_status["running"] = True
    try:
        logger.info("Starting scheduled scrape of all states")
        from backend.scraper.runner import run_all
        results = await run_all()
        scrape_status["last_results"] = results
        import datetime
        scrape_status["last_run"] = datetime.datetime.utcnow().isoformat()
        logger.info("Scheduled scrape complete")
    finally:
        scrape_status["running"] = False


async def scheduled_ma_retailer_scrape():
    logger.info("Starting weekly MA retailer scrape")
    try:
        from retailer_scraper import scrape_and_save_db
        from backend.ma_scorer import clear_cache
        async with get_pool().acquire() as conn:
            result = await scrape_and_save_db(conn)
        clear_cache()
        logger.info("MA retailer scrape complete: %s", result)
    except Exception as e:
        logger.error("MA retailer scrape failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_caller_db()
    await init_users_db()
    await seed_admin()

    # Schedule scrape every 6 hours (no startup trigger)
    scheduler.add_job(scheduled_scrape, "interval", hours=6, id="scrape_all")
    # MA retailer list changes slowly — re-scrape weekly
    scheduler.add_job(scheduled_ma_retailer_scrape, "interval", weeks=1, id="scrape_ma_retailers")

    # Attach call runner to scheduler
    runner = CallRunner()
    runner.attach_scheduler(scheduler)
    set_runner(runner)

    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="ScratchFever", description="Scratch-off lottery EV tracker", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(caller_webhook_router)
app.include_router(caller_api_router)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


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


@app.get("/api/games")
async def api_games(
    state: Optional[str] = Query(None, description="Filter by state code (e.g. TX)"),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    min_return: Optional[float] = Query(None, description="Minimum return % (e.g. 60)"),
    sort_by: str = Query("return_pct", description="Sort field"),
    limit: int = Query(500, le=1000),
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
        "prize_pool_remaining", "jackpot_odds_one_in",
    ]
    games = [dict(zip(cols, row)) for row in rows]
    return {"games": games, "count": len(games)}


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
    ]
    tier_cols = ["prize_amount", "odds_one_in", "prizes_total", "prizes_remaining"]
    all_cols = game_cols + tier_cols

    game = dict(zip(game_cols, rows[0][:len(game_cols)]))
    tiers = []
    for row in rows:
        tier_vals = row[len(game_cols):]
        if tier_vals[0] is not None:
            tiers.append(dict(zip(tier_cols, tier_vals)))

    tiers.sort(key=lambda t: t["prize_amount"] or 0, reverse=True)
    game["prize_tiers"] = tiers
    return game


@app.get("/api/states")
async def api_states():
    async with get_pool().acquire() as conn:
        rows = await get_states_summary(conn)
    cols = ["state_code", "state_name", "game_count", "last_scraped", "avg_return", "best_return"]
    return {"states": [dict(zip(cols, r)) for r in rows]}


@app.get("/api/status")
async def api_status():
    async with get_pool().acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM games WHERE is_active=TRUE")
        states = await conn.fetchval("SELECT COUNT(DISTINCT state_code) FROM games WHERE is_active=TRUE")
        rows = await conn.fetch(
            "SELECT ran_at, state_code, success, games_scraped FROM scrape_log ORDER BY ran_at DESC LIMIT 20"
        )
        log = [dict(zip(["ran_at", "state_code", "success", "games_scraped"], r)) for r in rows]
    return {
        "total_games": total,
        "states_covered": states,
        "scraper_running": scrape_status["running"],
        "last_run": scrape_status["last_run"],
        "recent_log": log,
    }


@app.post("/api/scrape")
async def api_trigger_scrape(
    background_tasks: BackgroundTasks,
    state: Optional[str] = Query(None, description="Scrape a single state (e.g. TX), or all if omitted"),
):
    if scrape_status["running"]:
        return {"message": "Scrape already in progress", "running": True}

    async def _run():
        scrape_status["running"] = True
        try:
            from backend.scraper.runner import run_all
            results = await run_all(state_filter=state)
            scrape_status["last_results"] = results
            import datetime
            scrape_status["last_run"] = datetime.datetime.utcnow().isoformat()
        finally:
            scrape_status["running"] = False

    background_tasks.add_task(_run)
    return {"message": f"Scrape started for {'all states' if not state else state}", "running": True}


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


@app.get("/api/prize-claims")
async def api_prize_claims(days: int = Query(7, le=30)):
    async with get_pool().acquire() as conn:
        claims = await get_recent_prize_claims(conn, days=days)
    for c in claims:
        if c.get("detected_at"):
            c["detected_at"] = c["detected_at"].isoformat()
    return {"claims": claims, "count": len(claims)}


@app.get("/api/az/retailers")
async def api_az_retailers(
    search: Optional[str] = Query(None, description="Name / city search"),
    limit:  int           = Query(500, le=7000),
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
    limit:  int           = Query(500, le=7000),
):
    from backend.ma_scorer import load_and_score_async
    async with get_pool().acquire() as conn:
        retailers = await load_and_score_async(conn)
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
    notes: str = ""
    reported_at: Optional[str] = None


@app.post("/api/inventory/report")
async def submit_inventory_report(
    body: InventoryReportBody,
    request: Request,
    user: dict = Depends(require_member),
):
    reporter_ip = request.client.host if request.client else None
    async with get_pool().acquire() as conn:
        if reporter_ip:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM inventory_reports WHERE reporter_ip=$1 AND reported_at > NOW() - INTERVAL '1 hour'",
                reporter_ip,
            )
            if count >= 10:
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
            source="community",
            reporter_ip=reporter_ip,
            reporter_username=user.get("username"),
            notes=body.notes or None,
            reported_at=body.reported_at or None,
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
    """Members-only: returns report counts per game name."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT LOWER(game_name), COUNT(*) FROM inventory_reports WHERE game_name IS NOT NULL GROUP BY LOWER(game_name)"
        )
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
async def get_retailer_latest_status(user: dict = Depends(require_member)):
    """Members-only: latest inventory status (has_stock + reported_at) per retailer."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (retailer_id) retailer_id, has_stock, reported_at
            FROM inventory_reports
            WHERE retailer_id IS NOT NULL
            ORDER BY retailer_id, reported_at DESC
        """)
    return {
        "statuses": {
            r["retailer_id"]: {"has_stock": r["has_stock"], "reported_at": r["reported_at"]}
            for r in rows
        }
    }
