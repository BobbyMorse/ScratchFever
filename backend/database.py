import asyncio
import asyncpg
import datetime as dt
import os
from typing import Optional

_pool: asyncpg.Pool | None = None
_games_cache: dict = {}


def clear_games_cache():
    global _games_cache
    _games_cache.clear()


async def add_column_if_missing(conn, table: str, column: str, type_def: str) -> bool:
    """Idempotent ADD COLUMN that takes no lock on the steady-state path.

    `ALTER TABLE … ADD COLUMN IF NOT EXISTS` still grabs an AccessExclusiveLock
    to evaluate the IF check, which deadlocks deploys when the old container's
    scraper is mid-INSERT. We check information_schema first (regular SELECT,
    no lock), and only run the ALTER on the rare "actually need to migrate"
    path — wrapped in a short lock_timeout so a real conflict fails fast
    instead of hanging the whole startup.

    Returns True if the ALTER ran, False if the column was already present.
    """
    exists = await conn.fetchval(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema = current_schema() AND table_name=$1 AND column_name=$2""",
        table, column,
    )
    if exists:
        return False
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout = '5s'")
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {type_def}")
    return True


async def init_db():
    global _pool
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL env var is not set — check Railway Variables tab")
    for attempt in range(1, 6):
        try:
            # DATABASE_URL points at Supabase's transaction-mode pooler
            # (port 6543), so the per-project ceiling is ~200 connections.
            # Default 25 gives the API real headroom; worker overrides via
            # DB_POOL_MAX_SIZE. statement_cache_size=0 below is required by
            # transaction mode (prepared statements don't survive connection
            # reuse across transactions).
            db_pool_max = int(os.environ.get("DB_POOL_MAX_SIZE", "25"))
            # min_size=1 so a new deploy can start even before the old deploy
            # releases connections — cheap insurance, costs nothing in steady state.
            _pool = await asyncpg.create_pool(
                db_url,
                min_size=1, max_size=db_pool_max,
                statement_cache_size=0,
                timeout=30,
            )
            break
        except Exception as e:
            if attempt == 5:
                raise
            wait = 2 ** attempt
            print(f"DB connect attempt {attempt} failed ({e}); retrying in {wait}s…")
            await asyncio.sleep(wait)
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id SERIAL PRIMARY KEY,
                state_code TEXT NOT NULL,
                state_name TEXT NOT NULL,
                game_id TEXT NOT NULL,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                ev REAL,
                return_pct REAL,
                overall_odds_one_in REAL,
                top_prize REAL,
                top_prize_remaining INTEGER,
                total_tickets INTEGER,
                tickets_remaining INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                detail_url TEXT,
                image_url TEXT,
                prize_pool_left REAL,
                jackpot_odds_one_in REAL,
                scraped_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(state_code, game_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS prize_tiers (
                id SERIAL PRIMARY KEY,
                game_db_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                prize_amount REAL NOT NULL,
                odds_one_in REAL,
                prizes_total INTEGER,
                prizes_remaining INTEGER
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_log (
                id SERIAL PRIMARY KEY,
                state_code TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                games_scraped INTEGER DEFAULT 0,
                error_msg TEXT,
                ran_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory_reports (
                id SERIAL PRIMARY KEY,
                retailer_id TEXT NOT NULL,
                retailer_name TEXT,
                retailer_city TEXT,
                lat REAL,
                lng REAL,
                game_name TEXT,
                game_price REAL,
                has_stock BOOLEAN NOT NULL,
                source TEXT NOT NULL DEFAULT 'community',
                reporter_ip TEXT,
                reporter_username TEXT,
                notes TEXT,
                state_code TEXT,
                reported_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_scrape_log_state_ran ON scrape_log(state_code, ran_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_state ON games(state_code)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_return ON games(return_pct DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_price ON games(price)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tiers_game ON prize_tiers(game_db_id)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS prize_claims (
                id SERIAL PRIMARY KEY,
                game_db_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                game_name TEXT NOT NULL,
                state_code TEXT NOT NULL,
                prize_amount REAL NOT NULL,
                tier_rank INTEGER NOT NULL,
                prev_remaining INTEGER NOT NULL,
                new_remaining INTEGER NOT NULL,
                claimed_count INTEGER NOT NULL,
                detected_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ir_retailer ON inventory_reports(retailer_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ir_reported ON inventory_reports(reported_at DESC)")
        # Reporter device geo (separate from retailer's known lat/lng — used for
        # bounty geo-verification so we can confirm the user was physically near
        # the store when they submitted).
        await add_column_if_missing(conn, "inventory_reports", "reporter_lat", "REAL")
        await add_column_if_missing(conn, "inventory_reports", "reporter_lng", "REAL")
        # Bounty session id ties a batch of reports to one display-scan session,
        # so the claim endpoint can count "reports this user just submitted for
        # this store" without re-counting historical submissions.
        await add_column_if_missing(conn, "inventory_reports", "bounty_session", "TEXT")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ir_bounty_session ON inventory_reports(reporter_username, retailer_id, reported_at DESC) WHERE bounty_session IS NOT NULL")
        # state_code disambiguates cross-state retailer_id collisions (MA's
        # ma_retailers.id and RI's state_retailers.external_id both use small
        # integers — id "9482" exists as a real store in BOTH states). Without
        # this, a VAPI call to an MA store would surface as inventory on a
        # different RI store on the mobile map. Filter retailer-latest /
        # game-counts / retailer-counts on it.
        await add_column_if_missing(conn, "inventory_reports", "state_code", "TEXT")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ir_state_retailer ON inventory_reports(state_code, retailer_id)")
        # Bounty claim ledger. One row per granted reward — enforces the per-user
        # per-store cooldown (a user can only claim a bounty on a given store
        # once every COOLDOWN_DAYS).
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bounty_claims (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                retailer_id TEXT NOT NULL,
                state_code TEXT,
                granted_days INTEGER NOT NULL,
                photos_count INTEGER NOT NULL,
                distinct_games INTEGER NOT NULL,
                session_id TEXT,
                claimed_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bc_user_retailer ON bounty_claims(user_id, retailer_id, claimed_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bc_retailer_claimed ON bounty_claims(retailer_id, claimed_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_detected ON prize_claims(detected_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_prize_detected ON prize_claims(prize_amount, detected_at DESC)")
        await add_column_if_missing(conn, "games", "jackpot_odds_one_in", "REAL")
        await add_column_if_missing(conn, "games", "how_to_play", "TEXT")
        await add_column_if_missing(conn, "games", "end_date", "DATE")
        await add_column_if_missing(conn, "games", "ev_approximate", "BOOLEAN DEFAULT FALSE")
        # Game launch date — enables sell-through velocity (tickets/day) and "days on sale" UI.
        await add_column_if_missing(conn, "games", "start_date", "DATE")
        # Annuity metadata for the top prize. cash_value is what EV math uses; face stays in top_prize.
        await add_column_if_missing(conn, "games", "top_prize_is_annuity", "BOOLEAN DEFAULT FALSE")
        await add_column_if_missing(conn, "games", "top_prize_cash_value", "REAL")
        await add_column_if_missing(conn, "games", "top_prize_annuity_years", "INTEGER")
        await add_column_if_missing(conn, "games", "top_prize_annuity_annual", "REAL")
        # Second-chance drawing surface — populated per-game from each state's
        # actual second-chance promotions list, not blanket-flagged.
        await add_column_if_missing(conn, "games", "has_second_chance", "BOOLEAN DEFAULT FALSE")
        await add_column_if_missing(conn, "games", "second_chance_url", "TEXT")
        # One-time purge: a prior change blanket-set has_second_chance=TRUE for
        # every MA/NY game and 44 other states via a runner-level overlay,
        # which was inaccurate (only a subset of games are in each state's
        # second-chance promotion). Both blanket flags have been reverted in
        # the scrapers; this clears the stale rows immediately rather than
        # waiting for each state's next scrape cycle.
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())")
        first_run = await conn.fetchval(
            "INSERT INTO schema_meta (key) VALUES ($1) ON CONFLICT (key) DO NOTHING RETURNING key",
            "purge_blanket_second_chance_2026_06_12",
        )
        if first_run:
            res = await conn.execute(
                "UPDATE games SET has_second_chance=FALSE, second_chance_url=NULL "
                "WHERE has_second_chance=TRUE OR second_chance_url IS NOT NULL"
            )
            logger.warning("Purged blanket has_second_chance flags: %s", res)
        # State-published per-tier claim date (distinct from prize_claims delta detection).
        await add_column_if_missing(conn, "prize_tiers", "last_claimed_at", "TIMESTAMPTZ")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS game_weekly_sales (
                id SERIAL PRIMARY KEY,
                state_code TEXT NOT NULL,
                game_id TEXT NOT NULL,
                game_db_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
                week_ending DATE NOT NULL,
                tickets_sold BIGINT,
                dollars_sold REAL,
                source_url TEXT,
                scraped_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(state_code, game_id, week_ending)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_weekly_sales_game ON game_weekly_sales(state_code, game_id, week_ending DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_weekly_sales_db ON game_weekly_sales(game_db_id, week_ending DESC)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS second_chance_drawings (
                id SERIAL PRIMARY KEY,
                state_code TEXT NOT NULL,
                drawing_id TEXT NOT NULL,
                drawing_name TEXT,
                drawing_date DATE,
                prize_description TEXT,
                prize_pool REAL,
                game_ids TEXT[],
                detail_url TEXT,
                scraped_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(state_code, drawing_id)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_sc_state_date ON second_chance_drawings(state_code, drawing_date DESC)")
        # Clear stale past end_dates for states whose scrapers no longer set end_date (e.g. CA).
        # Active games with a past end_date were set by an older scraper version and should not
        # suppress display. Wrapped in lock_timeout so a mid-INSERT scraper from the previous
        # deploy can't block startup past the Railway healthcheck window.
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout = '5s'")
                await conn.execute(
                    "UPDATE games SET end_date = NULL WHERE is_active = TRUE AND end_date < CURRENT_DATE"
                )
        except asyncpg.exceptions.LockNotAvailableError:
            import logging
            logging.getLogger(__name__).warning("startup: skipping end_date cleanup, games lock contended")
        # CA API returns number=0 for all prize tiers when remaining counts are unavailable,
        # causing tickets_remaining to be stored as 0 instead of NULL. The UltraRare filter
        # then removes all CA games (0 < 30000, not null). Clear the bogus zeros.
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout = '5s'")
                await conn.execute(
                    "UPDATE games SET tickets_remaining = NULL WHERE state_code = 'CA' AND tickets_remaining = 0"
                )
        except asyncpg.exceptions.LockNotAvailableError:
            import logging
            logging.getLogger(__name__).warning("startup: skipping CA tickets_remaining cleanup, games lock contended")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS state_retailers (
                id SERIAL PRIMARY KEY,
                state_code TEXT NOT NULL,
                external_id TEXT NOT NULL,
                name TEXT NOT NULL,
                address TEXT,
                city TEXT,
                zip_code TEXT,
                phone TEXT,
                latitude REAL,
                longitude REAL,
                is_active BOOLEAN DEFAULT TRUE,
                scraped_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(state_code, external_id)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_state_retailers_state ON state_retailers(state_code)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_state_retailers_geo ON state_retailers(latitude, longitude)")
        # geo_approximated: TRUE when lat/lon comes from a ZIP-centroid or
        # state-centroid fallback (source feed was out of bbox AND Census
        # couldn't place the address). Lets the UI show a softer pin/marker
        # for "rough location" so users don't trust it as precise.
        await add_column_if_missing(conn, "state_retailers", "geo_approximated", "BOOLEAN DEFAULT FALSE")
        for _tbl in ("ma_retailers", "az_retailers", "fl_retailers", "ga_retailers", "ny_retailers", "ri_retailers"):
            try:
                await add_column_if_missing(conn, _tbl, "geo_approximated", "BOOLEAN DEFAULT FALSE")
            except asyncpg.exceptions.UndefinedTableError:
                pass
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS retailer_scrape_log (
                state_code TEXT PRIMARY KEY,
                last_scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                retailers_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_plays (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                game_name TEXT NOT NULL,
                game_db_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
                state_code TEXT,
                price_paid REAL NOT NULL,
                prize_won REAL NOT NULL DEFAULT 0,
                retailer_name TEXT,
                notes TEXT,
                played_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_plays_user ON user_plays(user_id, played_at DESC)")
        # Per-user scratch-ticket scan history — server mirror of the mobile
        # app's local AsyncStorage. Client-generated `client_id` (uuid) is the
        # sync key so reinstalls/cross-device merge cleanly. updated_at drives
        # last-writer-wins conflict resolution.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_tickets (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                client_id TEXT NOT NULL,
                scanned_at TIMESTAMPTZ NOT NULL,
                game_name TEXT NOT NULL,
                ticket_number TEXT,
                state TEXT,
                won BOOLEAN,
                prize_amount REAL,
                ticket_price REAL,
                game_return_pct REAL,
                game_top_prize REAL,
                game_jackpot_odds_one_in REAL,
                game_ev REAL,
                game_has_second_chance BOOLEAN,
                notes TEXT,
                raw_ocr_text TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, client_id)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_tickets_user ON user_tickets(user_id, scanned_at DESC)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reported_wins (
                id SERIAL PRIMARY KEY,
                state_code TEXT NOT NULL,
                source_game_id TEXT,
                source_game_name TEXT,
                game_db_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
                prize_amount REAL NOT NULL,
                claim_date DATE,
                winner_city TEXT,
                retailer_name TEXT,
                retailer_address TEXT,
                retailer_city TEXT,
                retailer_zip TEXT,
                retailer_lat REAL,
                retailer_lng REAL,
                source_url TEXT,
                source_id TEXT NOT NULL,
                scraped_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(state_code, source_id)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rw_state_date ON reported_wins(state_code, claim_date DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rw_prize ON reported_wins(prize_amount DESC, claim_date DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rw_game ON reported_wins(game_db_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rw_geo ON reported_wins(retailer_lat, retailer_lng) WHERE retailer_lat IS NOT NULL")

        # Per-state scrape attempt log. Distinguishes "scraper crashed" from
        # "scraper ran fine but source had no new $10K+ wins in window" — both
        # used to look identical because MAX(scraped_at) on reported_wins only
        # advances when a row is actually upserted, so a healthy scraper hitting
        # a quiet source appeared "broken" in the admin dashboard.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS winners_scrape_log (
                state_code TEXT PRIMARY KEY,
                last_attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_success_at TIMESTAMPTZ,
                rows_last_run INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
        """)


async def init_retailer_db():
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS retailer_profiles (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                retailer_id TEXT NOT NULL,
                state_code TEXT NOT NULL DEFAULT 'MA',
                store_name TEXT NOT NULL,
                city TEXT,
                zip TEXT,
                phone TEXT,
                verified BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Owner-editable profile fields, added incrementally so existing prod rows stay intact.
        await add_column_if_missing(conn, "retailer_profiles", "description", "TEXT")
        await add_column_if_missing(conn, "retailer_profiles", "website", "TEXT")
        await add_column_if_missing(conn, "retailer_profiles", "contact_email", "TEXT")
        await add_column_if_missing(conn, "retailer_profiles", "hours_text", "TEXT")
        await add_column_if_missing(conn, "retailer_profiles", "photo_url", "TEXT")
        await add_column_if_missing(conn, "retailer_profiles", "banner_text", "TEXT")
        await add_column_if_missing(conn, "retailer_profiles", "banner_until", "TIMESTAMPTZ")
        await add_column_if_missing(conn, "retailer_profiles", "updated_at", "TIMESTAMPTZ DEFAULT NOW()")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS retailer_posts (
                id SERIAL PRIMARY KEY,
                retailer_id TEXT NOT NULL,
                store_name TEXT,
                title TEXT NOT NULL,
                body TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rprofile_user ON retailer_profiles(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rposts_retailer ON retailer_posts(retailer_id)")

        # Claim requests — a user submits, an admin approves/rejects.
        # On approve we create the retailer_profiles row and bump the user's role to 'retailer'.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS retailer_claims (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                retailer_id TEXT NOT NULL,
                state_code TEXT NOT NULL,
                store_name TEXT NOT NULL,
                city TEXT,
                zip TEXT,
                phone TEXT,
                claimant_role TEXT,
                claimant_name TEXT,
                claimant_phone TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                reviewed_at TIMESTAMPTZ,
                reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                review_notes TEXT
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rclaims_user ON retailer_claims(user_id, created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rclaims_status ON retailer_claims(status, created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_rclaims_retailer ON retailer_claims(state_code, retailer_id)")


def get_pool() -> asyncpg.Pool:
    return _pool


async def upsert_game(conn: asyncpg.Connection, state_code: str, state_name: str, game_id: str, game_data: dict) -> int:
    import datetime
    raw_end = game_data.get("end_date")
    if isinstance(raw_end, str):
        try:
            raw_end = datetime.date.fromisoformat(raw_end)
        except ValueError:
            raw_end = None
    raw_start = game_data.get("start_date")
    if isinstance(raw_start, str):
        try:
            raw_start = datetime.date.fromisoformat(raw_start)
        except ValueError:
            raw_start = None
    row = await conn.fetchrow("""
        INSERT INTO games (state_code, state_name, game_id, name, price, ev, return_pct,
            overall_odds_one_in, top_prize, top_prize_remaining,
            total_tickets, tickets_remaining, prize_pool_left, jackpot_odds_one_in,
            detail_url, image_url, how_to_play, end_date, ev_approximate,
            start_date, top_prize_is_annuity, top_prize_cash_value,
            top_prize_annuity_years, top_prize_annuity_annual,
            has_second_chance, second_chance_url,
            scraped_at, is_active)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19,
                $20, $21, $22, $23, $24, $25, $26, NOW(), TRUE)
        ON CONFLICT(state_code, game_id) DO UPDATE SET
            -- Dynamic fields: must overwrite every scrape (even to NULL).
            -- ev/return_pct/jackpot_odds_one_in are recomputed; remaining
            -- counters legitimately move; ev_approximate reflects this run.
            ev=EXCLUDED.ev,
            return_pct=EXCLUDED.return_pct,
            top_prize_remaining=EXCLUDED.top_prize_remaining,
            tickets_remaining=EXCLUDED.tickets_remaining,
            prize_pool_left=EXCLUDED.prize_pool_left,
            jackpot_odds_one_in=EXCLUDED.jackpot_odds_one_in,
            ev_approximate=EXCLUDED.ev_approximate,
            top_prize_is_annuity=EXCLUDED.top_prize_is_annuity,
            has_second_chance=EXCLUDED.has_second_chance,
            -- Static / metadata fields: COALESCE so a glitched scrape that
            -- returns NULL can never wipe the prior value. These are fixed
            -- at game launch (or are URLs/images we'd rather keep stale
            -- than lose entirely).
            name=COALESCE(EXCLUDED.name, games.name),
            price=COALESCE(EXCLUDED.price, games.price),
            overall_odds_one_in=COALESCE(EXCLUDED.overall_odds_one_in, games.overall_odds_one_in),
            top_prize=COALESCE(EXCLUDED.top_prize, games.top_prize),
            total_tickets=COALESCE(EXCLUDED.total_tickets, games.total_tickets),
            detail_url=COALESCE(EXCLUDED.detail_url, games.detail_url),
            image_url=COALESCE(EXCLUDED.image_url, games.image_url),
            how_to_play=COALESCE(EXCLUDED.how_to_play, games.how_to_play),
            end_date=COALESCE(EXCLUDED.end_date, games.end_date),
            start_date=COALESCE(EXCLUDED.start_date, games.start_date),
            top_prize_cash_value=COALESCE(EXCLUDED.top_prize_cash_value, games.top_prize_cash_value),
            top_prize_annuity_years=COALESCE(EXCLUDED.top_prize_annuity_years, games.top_prize_annuity_years),
            top_prize_annuity_annual=COALESCE(EXCLUDED.top_prize_annuity_annual, games.top_prize_annuity_annual),
            second_chance_url=COALESCE(EXCLUDED.second_chance_url, games.second_chance_url),
            scraped_at=NOW(), is_active=TRUE
        RETURNING id
    """,
        state_code, state_name, game_id,
        game_data.get("name"), game_data.get("price"), game_data.get("ev"), game_data.get("return_pct"),
        game_data.get("overall_odds_one_in"), game_data.get("top_prize"), game_data.get("top_prize_remaining"),
        game_data.get("total_tickets"), game_data.get("tickets_remaining"),
        game_data.get("prize_pool_left"), game_data.get("jackpot_odds_one_in"),
        game_data.get("detail_url"), game_data.get("image_url"),
        game_data.get("how_to_play"), raw_end,
        bool(game_data.get("ev_approximate", False)),
        raw_start,
        bool(game_data.get("top_prize_is_annuity", False)),
        game_data.get("top_prize_cash_value"),
        game_data.get("top_prize_annuity_years"),
        game_data.get("top_prize_annuity_annual"),
        bool(game_data.get("has_second_chance", False)),
        game_data.get("second_chance_url"),
    )
    return row["id"]


async def upsert_prize_tiers(conn: asyncpg.Connection, game_db_id: int, tiers: list[dict]):
    # Snapshot all $10K+ tiers before wipe so we can detect claims
    old_rows = await conn.fetch(
        "SELECT prize_amount, prizes_remaining FROM prize_tiers WHERE game_db_id=$1 AND prize_amount >= 10000",
        game_db_id,
    )
    old_big = {r["prize_amount"]: r["prizes_remaining"] for r in old_rows if r["prizes_remaining"] is not None}

    await conn.execute("DELETE FROM prize_tiers WHERE game_db_id=$1", game_db_id)
    if tiers:
        rows = []
        for t in tiers:
            raw_lc = t.get("last_claimed_at")
            if isinstance(raw_lc, str):
                try:
                    raw_lc = dt.datetime.fromisoformat(raw_lc.replace("Z", "+00:00"))
                except ValueError:
                    raw_lc = None
            rows.append((
                game_db_id, t.get("prize_amount"), t.get("odds_one_in"),
                t.get("prizes_total"), t.get("prizes_remaining"), raw_lc,
            ))
        await conn.executemany(
            "INSERT INTO prize_tiers (game_db_id, prize_amount, odds_one_in, prizes_total, prizes_remaining, last_claimed_at) VALUES ($1, $2, $3, $4, $5, $6)",
            rows,
        )

    if not old_big or not tiers:
        return
    new_big = sorted(
        [t for t in tiers if (t.get("prize_amount") or 0) >= 10000 and t.get("prizes_remaining") is not None],
        key=lambda t: t["prize_amount"],
        reverse=True,
    )
    game_row = await conn.fetchrow("SELECT name, state_code FROM games WHERE id=$1", game_db_id)
    if not game_row:
        return
    recent_claims = await conn.fetch(
        """SELECT prize_amount FROM prize_claims
           WHERE game_db_id=$1 AND detected_at >= NOW() - INTERVAL '24 hours'""",
        game_db_id,
    )
    recently_logged = {r["prize_amount"] for r in recent_claims}

    for rank, tier in enumerate(new_big, start=1):
        amt = tier["prize_amount"]
        new_rem = tier["prizes_remaining"]
        prev_rem = old_big.get(amt)
        if prev_rem is not None and new_rem < prev_rem and amt not in recently_logged:
            await conn.execute(
                """INSERT INTO prize_claims
                   (game_db_id, game_name, state_code, prize_amount, tier_rank,
                    prev_remaining, new_remaining, claimed_count)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                game_db_id, game_row["name"], game_row["state_code"],
                amt, rank, prev_rem, new_rem, prev_rem - new_rem,
            )


async def get_all_games(conn, state=None, min_price=None, max_price=None,
                        min_return=None, sort_by="return_pct", limit=500):
    allowed_sorts = {"return_pct", "ev", "price", "name", "state_code", "top_prize", "top_prize_remaining", "start_date", "overall_odds_one_in", "jackpot_odds_one_in"}
    if sort_by not in allowed_sorts:
        sort_by = "return_pct"

    cache_key = (state, min_price, max_price, min_return, sort_by, limit)
    if cache_key in _games_cache:
        return _games_cache[cache_key]

    conditions = ["g.is_active = TRUE", "g.ev IS NOT NULL", "(g.end_date IS NULL OR g.end_date >= CURRENT_DATE)"]
    params = []

    if state:
        params.append(state.upper())
        conditions.append(f"g.state_code = ${len(params)}")
    if min_price is not None:
        params.append(min_price)
        conditions.append(f"g.price >= ${len(params)}")
    if max_price is not None:
        params.append(max_price)
        conditions.append(f"g.price <= ${len(params)}")
    if min_return is not None:
        params.append(min_return)
        conditions.append(f"g.return_pct >= ${len(params)}")

    params.append(limit)
    direction = "ASC" if sort_by in ("price", "name", "jackpot_odds_one_in") else "DESC"
    query = f"""
        SELECT g.id, g.state_code, g.state_name, g.game_id, g.name, g.price,
               g.ev, g.return_pct, g.overall_odds_one_in,
               g.top_prize, g.top_prize_remaining,
               g.total_tickets, g.tickets_remaining,
               g.detail_url, g.image_url, g.scraped_at,
               COALESCE(g.prize_pool_left, (SELECT SUM(pt.prize_amount * pt.prizes_remaining)
                FROM prize_tiers pt WHERE pt.game_db_id = g.id)) AS prize_pool_remaining,
               g.jackpot_odds_one_in,
               COALESCE(g.ev_approximate, FALSE) AS ev_approximate,
               g.start_date,
               COALESCE(g.top_prize_is_annuity, FALSE) AS top_prize_is_annuity,
               g.top_prize_cash_value,
               g.top_prize_annuity_years,
               g.top_prize_annuity_annual,
               COALESCE(g.has_second_chance, FALSE) AS has_second_chance,
               g.second_chance_url
        FROM games g
        WHERE {" AND ".join(conditions)}
        ORDER BY g.{sort_by} {direction}
        LIMIT ${len(params)}
    """
    result = [tuple(r) for r in await conn.fetch(query, *params)]
    _games_cache[cache_key] = result
    return result


async def get_game_detail(conn, game_db_id: int):
    rows = await conn.fetch("""
        SELECT g.id, g.state_code, g.state_name, g.game_id, g.name, g.price,
               g.ev, g.return_pct, g.overall_odds_one_in,
               g.top_prize, g.top_prize_remaining, g.total_tickets, g.tickets_remaining,
               g.prize_pool_left, g.is_active, g.detail_url, g.image_url, g.scraped_at,
               g.how_to_play, COALESCE(g.ev_approximate, FALSE) AS ev_approximate,
               g.start_date,
               COALESCE(g.top_prize_is_annuity, FALSE) AS top_prize_is_annuity,
               g.top_prize_cash_value, g.top_prize_annuity_years, g.top_prize_annuity_annual,
               COALESCE(g.has_second_chance, FALSE) AS has_second_chance,
               g.second_chance_url,
               pt.prize_amount, pt.odds_one_in, pt.prizes_total, pt.prizes_remaining,
               pt.last_claimed_at
        FROM games g
        LEFT JOIN prize_tiers pt ON pt.game_db_id = g.id
        WHERE g.id = $1
        ORDER BY pt.prize_amount DESC
    """, game_db_id)
    return [tuple(r) for r in rows]


async def upsert_weekly_sales(conn, state_code: str, rows: list[dict]) -> int:
    """rows: list of {game_id, week_ending (date|str), tickets_sold?, dollars_sold?, source_url?}."""
    if not rows:
        return 0
    games_by_id = {}
    g_rows = await conn.fetch(
        "SELECT id, game_id FROM games WHERE state_code=$1",
        state_code,
    )
    for r in g_rows:
        games_by_id[r["game_id"]] = r["id"]
    saved = 0
    for r in rows:
        we = r.get("week_ending")
        if isinstance(we, str):
            try:
                we = dt.date.fromisoformat(we)
            except ValueError:
                continue
        if we is None:
            continue
        gid = str(r.get("game_id") or "")
        if not gid:
            continue
        game_db_id = games_by_id.get(gid)
        try:
            await conn.execute("""
                INSERT INTO game_weekly_sales
                    (state_code, game_id, game_db_id, week_ending, tickets_sold, dollars_sold, source_url)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (state_code, game_id, week_ending) DO UPDATE SET
                    tickets_sold = EXCLUDED.tickets_sold,
                    dollars_sold = EXCLUDED.dollars_sold,
                    source_url = COALESCE(EXCLUDED.source_url, game_weekly_sales.source_url),
                    game_db_id = COALESCE(EXCLUDED.game_db_id, game_weekly_sales.game_db_id),
                    scraped_at = NOW()
            """, state_code, gid, game_db_id, we,
                r.get("tickets_sold"), r.get("dollars_sold"), r.get("source_url"))
            saved += 1
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("weekly_sales insert failed %s/%s: %s", state_code, gid, e)
    return saved


async def get_weekly_sales(conn, state_code: str, game_id: str, limit: int = 52):
    rows = await conn.fetch("""
        SELECT week_ending, tickets_sold, dollars_sold, source_url
        FROM game_weekly_sales
        WHERE state_code=$1 AND game_id=$2
        ORDER BY week_ending DESC
        LIMIT $3
    """, state_code, game_id, limit)
    return [dict(zip(["week_ending", "tickets_sold", "dollars_sold", "source_url"], r)) for r in rows]


async def upsert_second_chance(conn, state_code: str, drawings: list[dict]) -> int:
    """drawings: list of {drawing_id, drawing_name?, drawing_date?, prize_description?,
                          prize_pool?, game_ids? (list), detail_url?}."""
    if not drawings:
        return 0
    saved = 0
    for d in drawings:
        did = str(d.get("drawing_id") or "")
        if not did:
            continue
        dd = d.get("drawing_date")
        if isinstance(dd, str):
            try:
                dd = dt.date.fromisoformat(dd)
            except ValueError:
                dd = None
        try:
            await conn.execute("""
                INSERT INTO second_chance_drawings
                    (state_code, drawing_id, drawing_name, drawing_date,
                     prize_description, prize_pool, game_ids, detail_url)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (state_code, drawing_id) DO UPDATE SET
                    drawing_name = COALESCE(EXCLUDED.drawing_name, second_chance_drawings.drawing_name),
                    drawing_date = COALESCE(EXCLUDED.drawing_date, second_chance_drawings.drawing_date),
                    prize_description = COALESCE(EXCLUDED.prize_description, second_chance_drawings.prize_description),
                    prize_pool = COALESCE(EXCLUDED.prize_pool, second_chance_drawings.prize_pool),
                    game_ids = COALESCE(EXCLUDED.game_ids, second_chance_drawings.game_ids),
                    detail_url = COALESCE(EXCLUDED.detail_url, second_chance_drawings.detail_url),
                    scraped_at = NOW()
            """, state_code, did, d.get("drawing_name"), dd,
                d.get("prize_description"), d.get("prize_pool"),
                d.get("game_ids"), d.get("detail_url"))
            saved += 1
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("second_chance insert failed %s/%s: %s", state_code, did, e)
    return saved


async def get_second_chance(conn, state_code: str | None = None, upcoming_only: bool = True, limit: int = 100):
    conditions = []
    params: list = []
    if state_code:
        params.append(state_code.upper())
        conditions.append(f"state_code = ${len(params)}")
    if upcoming_only:
        conditions.append("(drawing_date IS NULL OR drawing_date >= CURRENT_DATE)")
    params.append(limit)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await conn.fetch(f"""
        SELECT id, state_code, drawing_id, drawing_name, drawing_date,
               prize_description, prize_pool, game_ids, detail_url
        FROM second_chance_drawings
        {where}
        ORDER BY drawing_date NULLS LAST, scraped_at DESC
        LIMIT ${len(params)}
    """, *params)
    cols = ["id", "state_code", "drawing_id", "drawing_name", "drawing_date",
            "prize_description", "prize_pool", "game_ids", "detail_url"]
    return [dict(zip(cols, r)) for r in rows]


async def get_states_summary(conn):
    rows = await conn.fetch("""
        SELECT state_code, state_name,
               COUNT(*) as game_count,
               MAX(scraped_at) as last_scraped,
               AVG(return_pct) as avg_return,
               MAX(return_pct) as best_return
        FROM games
        WHERE is_active=TRUE AND ev IS NOT NULL AND (end_date IS NULL OR end_date >= CURRENT_DATE)
        GROUP BY state_code, state_name
        ORDER BY state_name
    """)
    return [tuple(r) for r in rows]


async def log_scrape(conn, state_code: str, success: bool, games_scraped: int = 0, error_msg: str = None):
    await conn.execute(
        "INSERT INTO scrape_log (state_code, success, games_scraped, error_msg) VALUES ($1, $2, $3, $4)",
        state_code, success, games_scraped, error_msg,
    )


async def add_inventory_report(conn, retailer_id: str, retailer_name: str = None,
                                retailer_city: str = None, lat: float = None, lng: float = None,
                                game_name: str = None, game_price: float = None,
                                has_stock: bool = False, source: str = "community",
                                reporter_ip: str = None, reporter_username: str = None,
                                notes: str = None, reported_at=None,
                                reporter_lat: float = None, reporter_lng: float = None,
                                bounty_session: str = None, state_code: str = None):
    if isinstance(reported_at, str):
        try:
            reported_at = dt.datetime.fromisoformat(reported_at.replace("Z", "+00:00"))
        except ValueError:
            reported_at = None
    if state_code:
        state_code = state_code.strip().upper() or None
    await conn.execute("""
        INSERT INTO inventory_reports
        (retailer_id, retailer_name, retailer_city, lat, lng,
         game_name, game_price, has_stock, source, reporter_ip, reporter_username, notes, reported_at,
         reporter_lat, reporter_lng, bounty_session, state_code)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, COALESCE($13, NOW()), $14, $15, $16, $17)
    """, retailer_id, retailer_name, retailer_city, lat, lng,
         game_name, game_price, has_stock, source, reporter_ip, reporter_username, notes, reported_at,
         reporter_lat, reporter_lng, bounty_session, state_code)


async def get_recent_prize_claims(conn, days: int = 7, limit: int = 200, min_prize: float = 0):
    rows = await conn.fetch("""
        SELECT id, game_db_id, game_name, state_code, prize_amount, tier_rank,
               prev_remaining, new_remaining, claimed_count, detected_at
        FROM prize_claims
        WHERE detected_at >= NOW() - make_interval(days => $1)
          AND prize_amount >= $3
        ORDER BY detected_at DESC
        LIMIT $2
    """, days, limit, min_prize)
    cols = ["id", "game_db_id", "game_name", "state_code", "prize_amount", "tier_rank",
            "prev_remaining", "new_remaining", "claimed_count", "detected_at"]
    return [dict(zip(cols, r)) for r in rows]


async def upsert_reported_wins(conn, state_code: str, wins: list[dict]) -> int:
    """
    Upsert reported wins for a state. Each win dict must include source_id (unique
    within state_code). Returns count of rows inserted or updated.

    Game-link strategy:
      1. If source_game_id is set, look up games.game_id for that state.
      2. Otherwise (fallback for states without a stable game id), fuzzy-match by
         normalized game name.

    Retailer-geo strategy:
      If retailer_lat/lng are None and a state_retailers row exists matching
      name + city, fill them in from there.
    """
    if not wins:
        return 0

    games_by_id = {}
    games_by_name = {}
    rows = await conn.fetch(
        "SELECT id, game_id, name FROM games WHERE state_code = $1 AND is_active = TRUE",
        state_code,
    )
    for r in rows:
        games_by_id[r["game_id"]] = r["id"]
        games_by_name[_norm_game_name(r["name"])] = r["id"]

    retailer_lookup, retailer_substr = await _load_retailer_geo_index(conn, state_code)

    # Resolve per-row lookups in Python, then batch-insert via executemany.
    rows_to_insert: list[tuple] = []
    for w in wins:
        game_db_id = None
        sgi = w.get("source_game_id")
        if sgi and sgi in games_by_id:
            game_db_id = games_by_id[sgi]
        else:
            normname = _norm_game_name(w.get("source_game_name") or "")
            if normname and normname in games_by_name:
                game_db_id = games_by_name[normname]

        lat = w.get("retailer_lat")
        lng = w.get("retailer_lng")
        if lat is None or lng is None:
            norm_name = _norm_retailer_name(w.get("retailer_name") or "")
            norm_city = (w.get("retailer_city") or "").lower().strip()
            if norm_name and norm_city:
                hit = retailer_lookup.get((norm_name, norm_city))
                if not hit:
                    candidates = retailer_substr.get(norm_city) or []
                    for cand_name, cand_lat, cand_lng in candidates:
                        if norm_name in cand_name or cand_name in norm_name:
                            hit = (cand_lat, cand_lng)
                            break
                if hit:
                    lat, lng = hit
        # Geocoding fallbacks when we couldn't match a specific retailer:
        #   1. If retailer_city is set, use that city's centroid.
        #   2. Otherwise fall back to winner_city (home-city pin).
        if lat is None or lng is None:
            from backend.scraper.winners.city_geocoder import geocode_city
            fallback_city = w.get("retailer_city") or w.get("winner_city")
            if fallback_city:
                hit = geocode_city(fallback_city, state_code)
                if hit:
                    lat, lng = hit

        rows_to_insert.append((
            state_code, sgi, w.get("source_game_name"), game_db_id,
            w.get("prize_amount"), w.get("claim_date"), w.get("winner_city"),
            w.get("retailer_name"), w.get("retailer_address"),
            w.get("retailer_city"), w.get("retailer_zip"),
            lat, lng, w.get("source_url"), w["source_id"],
        ))

    if not rows_to_insert:
        return 0

    sql = """
        INSERT INTO reported_wins
            (state_code, source_game_id, source_game_name, game_db_id,
             prize_amount, claim_date, winner_city,
             retailer_name, retailer_address, retailer_city, retailer_zip,
             retailer_lat, retailer_lng, source_url, source_id, scraped_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,NOW())
        ON CONFLICT (state_code, source_id) DO UPDATE SET
            game_db_id       = COALESCE(EXCLUDED.game_db_id, reported_wins.game_db_id),
            retailer_lat     = COALESCE(EXCLUDED.retailer_lat, reported_wins.retailer_lat),
            retailer_lng     = COALESCE(EXCLUDED.retailer_lng, reported_wins.retailer_lng),
            -- Refresh retailer/winner fields on every upsert. Prior versions
            -- of scrapers may have inserted partial rows (e.g. missing
            -- retailer_city), and the latest scrape's value should win.
            retailer_name    = COALESCE(EXCLUDED.retailer_name, reported_wins.retailer_name),
            retailer_address = COALESCE(EXCLUDED.retailer_address, reported_wins.retailer_address),
            retailer_city    = COALESCE(EXCLUDED.retailer_city, reported_wins.retailer_city),
            retailer_zip     = COALESCE(EXCLUDED.retailer_zip, reported_wins.retailer_zip),
            winner_city      = COALESCE(EXCLUDED.winner_city, reported_wins.winner_city),
            source_game_name = COALESCE(EXCLUDED.source_game_name, reported_wins.source_game_name),
            -- Refresh claim_date so scrapers can correct placeholder dates
            -- (e.g. PA/MO upgrading from first-of-month to end-of-month).
            claim_date       = COALESCE(EXCLUDED.claim_date, reported_wins.claim_date),
            scraped_at = NOW()
    """
    BATCH = 1000
    saved = 0
    import logging as _logging
    _log = _logging.getLogger(__name__)
    for i in range(0, len(rows_to_insert), BATCH):
        chunk = rows_to_insert[i:i + BATCH]
        try:
            await conn.executemany(sql, chunk)
            saved += len(chunk)
        except Exception as e:
            _log.warning("reported_wins batch %d-%d failed (%s) — falling back to per-row",
                         i, i + len(chunk), e)
            for params in chunk:
                try:
                    await conn.execute(sql, *params)
                    saved += 1
                except Exception as inner:
                    _log.warning("reported_wins row insert failed for %s/%s: %s",
                                 state_code, params[14], inner)
    return saved


async def upsert_winners_scrape_log(conn, state_code: str, rows: int, error: str | None) -> None:
    """Record a winners-scrape attempt for the admin dashboard.

    `rows` is the count returned by the scraper for this run (0 is valid when
    the source is quiet). `error` is None on success or a short message on
    failure. `last_success_at` only advances when error is None — that's the
    field the dashboard uses to tell "scraper is healthy, source quiet" from
    "scraper is broken".
    """
    await conn.execute(
        """
        INSERT INTO winners_scrape_log (state_code, last_attempted_at, last_success_at,
                                        rows_last_run, last_error)
        VALUES ($1, NOW(), CASE WHEN $3::TEXT IS NULL THEN NOW() ELSE NULL END, $2, $3)
        ON CONFLICT (state_code) DO UPDATE SET
            last_attempted_at = NOW(),
            last_success_at = CASE
                WHEN $3::TEXT IS NULL THEN NOW()
                ELSE winners_scrape_log.last_success_at
            END,
            rows_last_run = $2,
            last_error = $3
        """,
        state_code, rows, error,
    )


_PER_STATE_RETAILER_TABLES = {
    "MA": "ma_retailers",
    "AZ": "az_retailers",
    "FL": "fl_retailers",
    "GA": "ga_retailers",
    "RI": "ri_retailers",
}


async def _load_retailer_geo_index(conn, state_code: str):
    """
    Returns (exact_map, by_city) where:
      exact_map: {(norm_name, lower_city): (lat, lng)}
      by_city:   {lower_city: [(norm_name, lat, lng), ...]}  for substring fallback
    Pulls from the per-state retailer table when one exists, else state_retailers.
    """
    exact: dict[tuple[str, str], tuple[float, float]] = {}
    by_city: dict[str, list[tuple[str, float, float]]] = {}
    table = _PER_STATE_RETAILER_TABLES.get(state_code.upper())
    try:
        if table:
            rows = await conn.fetch(
                f"SELECT name, city, latitude, longitude FROM {table} "
                f"WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
            )
        else:
            rows = await conn.fetch(
                """SELECT name, city, latitude, longitude FROM state_retailers
                   WHERE state_code = $1 AND latitude IS NOT NULL AND longitude IS NOT NULL""",
                state_code,
            )
    except Exception:
        rows = []
    for r in rows:
        nname = _norm_retailer_name(r["name"])
        ncity = (r["city"] or "").lower().strip()
        if not nname:
            continue
        exact[(nname, ncity)] = (r["latitude"], r["longitude"])
        by_city.setdefault(ncity, []).append((nname, r["latitude"], r["longitude"]))
    return exact, by_city


def _norm_game_name(s: str) -> str:
    if not s:
        return ""
    import re
    s = s.lower().strip()
    s = re.sub(r'[\$,]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def _norm_retailer_name(s: str) -> str:
    if not s:
        return ""
    import re
    s = s.lower().strip()
    s = re.sub(r'\b(inc|llc|corp|co|the|&)\b', '', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


async def get_reported_wins(conn, days: int = 30, min_prize: float = 10000,
                             state: str | None = None, has_location: bool = False,
                             limit: int = 1000):
    conditions = ["claim_date >= CURRENT_DATE - make_interval(days => $1)",
                  "prize_amount >= $2"]
    params: list = [days, min_prize]
    if state:
        params.append(state.upper())
        conditions.append(f"state_code = ${len(params)}")
    if has_location:
        conditions.append("retailer_lat IS NOT NULL AND retailer_lng IS NOT NULL")
    params.append(limit)
    rows = await conn.fetch(f"""
        SELECT id, state_code, source_game_id, source_game_name, game_db_id,
               prize_amount, claim_date, winner_city,
               retailer_name, retailer_address, retailer_city, retailer_zip,
               retailer_lat, retailer_lng, source_url, scraped_at
        FROM reported_wins
        WHERE {' AND '.join(conditions)}
        ORDER BY claim_date DESC, prize_amount DESC
        LIMIT ${len(params)}
    """, *params)
    cols = ["id", "state_code", "source_game_id", "source_game_name", "game_db_id",
            "prize_amount", "claim_date", "winner_city",
            "retailer_name", "retailer_address", "retailer_city", "retailer_zip",
            "retailer_lat", "retailer_lng", "source_url", "scraped_at"]
    return [dict(zip(cols, r)) for r in rows]


async def get_reported_wins_for_map(conn, days: int, min_prize: float):
    """Return all geocoded reported wins matching (days, min_prize), no row cap.
    Used by the Big Wins map. Caller aggregates per location. Bounded in practice
    by retailer_lat IS NOT NULL — the map can't plot rows without coordinates."""
    return await conn.fetch("""
        SELECT state_code, source_game_name, game_db_id,
               prize_amount, claim_date, winner_city,
               retailer_name, retailer_city, retailer_lat, retailer_lng
        FROM reported_wins
        WHERE claim_date >= CURRENT_DATE - make_interval(days => $1)
          AND prize_amount >= $2
          AND retailer_lat IS NOT NULL
          AND retailer_lng IS NOT NULL
        ORDER BY prize_amount DESC, claim_date DESC
    """, days, min_prize)


async def get_recent_inventory_reports(conn, limit: int = 200,
                                        retailer_id: str = None, game_name: str = None):
    conditions = []
    params = []
    if retailer_id:
        params.append(retailer_id)
        conditions.append(f"retailer_id = ${len(params)}")
    if game_name:
        params.append(f"%{game_name}%")
        conditions.append(f"game_name ILIKE ${len(params)}")
    params.append(limit)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await conn.fetch(f"""
        SELECT id, retailer_id, retailer_name, retailer_city, lat, lng,
               game_name, game_price, has_stock, source, reporter_username, notes, reported_at
        FROM inventory_reports
        {where}
        ORDER BY reported_at DESC
        LIMIT ${len(params)}
    """, *params)
    cols = ["id", "retailer_id", "retailer_name", "retailer_city", "lat", "lng",
            "game_name", "game_price", "has_stock", "source", "reporter_username", "notes", "reported_at"]
    out = [dict(zip(cols, r)) for r in rows]
    # Defense in depth: never expose the reporter username or notes for
    # operator-side automated sources (vapi_call). The frontend already
    # hides these, but redacting at the API too means scrapers/API users
    # never see AI-implementation tells or paraphrased customer speech.
    for row in out:
        if row.get("source") == "vapi_call":
            row["reporter_username"] = None
            row["notes"] = None
    return out
