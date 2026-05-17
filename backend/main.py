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
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.database import (
    init_db, init_retailer_db, get_pool, get_all_games, get_game_detail, get_states_summary,
    add_inventory_report, get_recent_inventory_reports, get_recent_prize_claims,
    clear_games_cache,
)
from backend.caller.db import init_caller_db
from backend.caller.webhook import router as caller_webhook_router
from backend.caller.api import router as caller_api_router, set_runner
from backend.caller.runner import CallRunner
from backend.users import init_users_db, seed_admin, require_member, require_admin
from backend.auth_api import router as auth_router
from backend.retailer_api import router as retailer_router, public_router as retailer_public_router
from backend.plays_api import router as plays_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
scrape_status = {"running": False, "last_run": None, "last_results": [], "current_state": None}


SCRAPE_COOLDOWN_SEC = 300  # 5-min pause between full crawl cycles


async def scheduled_scrape():
    if scrape_status["running"]:
        logger.info("Scrape already running, skipping")
        return
    scrape_status["running"] = True
    try:
        logger.info("Starting scrape of all states")
        from backend.scraper.runner import run_all
        results = await run_all()
        scrape_status["last_results"] = results
        scrape_status["last_run"] = datetime.datetime.utcnow().isoformat()
        clear_games_cache()
        logger.info("Scrape complete — next cycle in %ds", SCRAPE_COOLDOWN_SEC)
    except Exception as e:
        logger.error("Scrape cycle failed: %s", e, exc_info=True)
    finally:
        scrape_status["running"] = False
        # Always re-schedule — even if the cycle crashed — so the scraper never dies
        scheduler.add_job(scheduled_scrape, "date",
                          run_date=datetime.datetime.utcnow() + datetime.timedelta(seconds=SCRAPE_COOLDOWN_SEC),
                          id="scrape_next", replace_existing=True)


async def check_and_run_stale_retailers():
    """Check retailer_scrape_log and scrape any state not updated in 30 days."""
    try:
        from backend.retailer_scrapers.runner import run_stale
        results = await run_stale(max_age_days=30)
        if results:
            logger.info("Retailer scrape complete: %s", results)
    except Exception as e:
        logger.error("Retailer staleness check failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_caller_db()
    await init_users_db()
    await init_retailer_db()
    await seed_admin()

    # Retailer freshness check — daily
    scheduler.add_job(check_and_run_stale_retailers, "interval", days=1, id="check_retailer_freshness")

    # Attach call runner to scheduler
    runner = CallRunner()
    runner.attach_scheduler(scheduler)
    set_runner(runner)

    scheduler.start()

    # Delay heavy startup work so Railway health checks pass before scraping begins
    STARTUP_DELAY = 90  # seconds after boot before first scrape/cache-warm

    async def _delayed_startup():
        await asyncio.sleep(STARTUP_DELAY)
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
        await scheduled_scrape()

    asyncio.create_task(_delayed_startup())

    yield
    scheduler.shutdown()


app = FastAPI(title="ScratchFever", description="Scratch-off lottery EV tracker", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(caller_webhook_router)
app.include_router(caller_api_router)
app.include_router(retailer_router)
app.include_router(retailer_public_router)
app.include_router(plays_router)

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


@app.get("/retailer", include_in_schema=False)
async def retailer_dashboard():
    return FileResponse(os.path.join(FRONTEND_DIR, "retailer.html"))


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin.html"))


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
        "how_to_play",
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
                ROUND(AVG(CASE WHEN ev IS NOT NULL THEN return_pct END)::numeric, 1) AS avg_return,
                ROUND(100.0 * COUNT(CASE WHEN tickets_remaining > 0 THEN 1 END) / NULLIF(COUNT(tickets_remaining),0), 0) AS prizes_pct
            FROM games WHERE is_active=TRUE
            GROUP BY state_code
        """)
        log_rows = await conn.fetch("""
            SELECT DISTINCT ON (state_code)
                state_code, success, ran_at
            FROM scrape_log
            ORDER BY state_code, ran_at DESC
        """)
        retailer_rows = await conn.fetch(
            "SELECT state_code, last_scraped_at FROM retailer_scrape_log"
        )

    games_by_state = {r["state_code"]: r["games_in_db"] for r in game_rows}
    log_by_state = {r["state_code"]: r for r in log_rows}

    states = []
    for state_code in sorted(all_known, key=lambda c: all_known[c]):
        state_name = all_known[state_code]
        log = log_by_state.get(state_code)
        games = games_by_state.get(state_code, 0)
        if log:
            status = "ok" if log["success"] else "error"
        elif games:
            status = "warn"
        else:
            status = "never"
        states.append({
            "state_code": state_code,
            "state_name": state_name,
            "games_in_db": games,
            "last_scrape_at": log["ran_at"].isoformat() if log else None,
            "status": status,
        })

    return {
        "states": states,
        "scraper_running": scrape_status["running"],
        "current_state": scrape_status.get("current_state"),
        "last_run": scrape_status.get("last_run"),
    }


@app.get("/api/status")
async def api_status():
    async with get_pool().acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM games WHERE is_active=TRUE")
        states = await conn.fetchval("SELECT COUNT(DISTINCT state_code) FROM games WHERE is_active=TRUE")
        rows = await conn.fetch(
            "SELECT ran_at, state_code, success, games_scraped FROM scrape_log ORDER BY ran_at DESC LIMIT 20"
        )
        log = [dict(zip(["ran_at", "state_code", "success", "games_scraped"], r)) for r in rows]
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
async def api_prize_claims(days: int = Query(7, le=30), min_prize: float = Query(0, ge=0)):
    cache_key = (days, min_prize)
    cached = _prize_claims_cache.get(cache_key)
    if cached and (datetime.datetime.utcnow().timestamp() - cached[0]) < _PRIZE_CLAIMS_TTL:
        return cached[1]
    async with get_pool().acquire() as conn:
        claims = await get_recent_prize_claims(conn, days=days, min_prize=min_prize)
    for c in claims:
        if c.get("detected_at"):
            c["detected_at"] = c["detected_at"].isoformat()
    result = {"claims": claims, "count": len(claims), "fetched_at": datetime.datetime.utcnow().isoformat() + "Z"}
    _prize_claims_cache[cache_key] = (datetime.datetime.utcnow().timestamp(), result)
    return result


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


@app.get("/api/scrape/status")
async def api_scrape_status():
    return {
        "running": scrape_status["running"],
        "last_run": scrape_status["last_run"],
        "last_results": scrape_status["last_results"],
    }


@app.get("/api/debug/fetch")
async def api_debug_fetch(url: str = Query(...)):
    """Temp debug: fetch a URL and return link counts to diagnose scraper issues."""
    import requests as _req
    from bs4 import BeautifulSoup as _BS
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = _req.get(url, headers=headers, timeout=30)
    soup = _BS(r.text, "lxml")
    all_links = soup.find_all("a", href=True)
    scratch_links = [a["href"] for a in all_links if "scratch" in a.get("href","").lower()]

    # Inspect first data-game-id element
    game_id_els = soup.select("[data-game-id]")
    game_id_info = []
    for el in game_id_els[:2]:
        child_classes = [" ".join(c.get("class", [])) for c in el.find_all(True)[:8]]
        headings = [h.get_text(strip=True) for h in el.find_all(["h2","h3","h4","h5","h6"])[:3]]
        prices = [p.get_text(strip=True) for p in el.find_all(["p","span","div"]) if "$" in p.get_text()][:3]
        game_id_info.append({
            "data-game-id": el.get("data-game-id"),
            "tag": el.name,
            "classes": " ".join(el.get("class", [])),
            "child_classes_sample": child_classes,
            "headings": headings,
            "price_texts": prices,
            "text_snippet": el.get_text(" ", strip=True)[:200],
        })

    return {
        "status": r.status_code,
        "total_links": len(all_links),
        "scratch_links": len(scratch_links),
        "sample_scratch_hrefs": scratch_links[:5],
        "page_length": len(r.text),
        "game_id_elements_found": len(game_id_els),
        "game_id_sample": game_id_info,
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
async def get_retailer_latest_status(
    game_name: Optional[str] = Query(None),
    user: dict = Depends(require_member),
):
    """Members-only: latest inventory status (has_stock + reported_at) per retailer.
    Pass game_name to filter to a specific game."""
    async with get_pool().acquire() as conn:
        if game_name:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (retailer_id) retailer_id, has_stock, reported_at
                FROM inventory_reports
                WHERE retailer_id IS NOT NULL AND LOWER(game_name) = LOWER($1)
                ORDER BY retailer_id, reported_at DESC
            """, game_name)
        else:
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
