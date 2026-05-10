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


async def init_db():
    global _pool
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL env var is not set — check Railway Variables tab")
    for attempt in range(1, 6):
        try:
            _pool = await asyncpg.create_pool(
                db_url,
                min_size=2, max_size=10,
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
                reported_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
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
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_detected ON prize_claims(detected_at DESC)")
        await conn.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS jackpot_odds_one_in REAL")


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


def get_pool() -> asyncpg.Pool:
    return _pool


async def upsert_game(conn: asyncpg.Connection, state_code: str, state_name: str, game_id: str, game_data: dict) -> int:
    row = await conn.fetchrow("""
        INSERT INTO games (state_code, state_name, game_id, name, price, ev, return_pct,
            overall_odds_one_in, top_prize, top_prize_remaining,
            total_tickets, tickets_remaining, prize_pool_left, jackpot_odds_one_in,
            detail_url, image_url, scraped_at, is_active)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, NOW(), TRUE)
        ON CONFLICT(state_code, game_id) DO UPDATE SET
            name=EXCLUDED.name, price=EXCLUDED.price, ev=EXCLUDED.ev,
            return_pct=EXCLUDED.return_pct, overall_odds_one_in=EXCLUDED.overall_odds_one_in,
            top_prize=EXCLUDED.top_prize, top_prize_remaining=EXCLUDED.top_prize_remaining,
            total_tickets=EXCLUDED.total_tickets, tickets_remaining=EXCLUDED.tickets_remaining,
            prize_pool_left=EXCLUDED.prize_pool_left, jackpot_odds_one_in=EXCLUDED.jackpot_odds_one_in,
            detail_url=EXCLUDED.detail_url, image_url=EXCLUDED.image_url, scraped_at=NOW(), is_active=TRUE
        RETURNING id
    """,
        state_code, state_name, game_id,
        game_data.get("name"), game_data.get("price"), game_data.get("ev"), game_data.get("return_pct"),
        game_data.get("overall_odds_one_in"), game_data.get("top_prize"), game_data.get("top_prize_remaining"),
        game_data.get("total_tickets"), game_data.get("tickets_remaining"),
        game_data.get("prize_pool_left"), game_data.get("jackpot_odds_one_in"),
        game_data.get("detail_url"), game_data.get("image_url"),
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
        await conn.executemany(
            "INSERT INTO prize_tiers (game_db_id, prize_amount, odds_one_in, prizes_total, prizes_remaining) VALUES ($1, $2, $3, $4, $5)",
            [(game_db_id, t.get("prize_amount"), t.get("odds_one_in"), t.get("prizes_total"), t.get("prizes_remaining")) for t in tiers],
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
    for rank, tier in enumerate(new_big, start=1):
        amt = tier["prize_amount"]
        new_rem = tier["prizes_remaining"]
        prev_rem = old_big.get(amt)
        if prev_rem is not None and new_rem < prev_rem:
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
    allowed_sorts = {"return_pct", "ev", "price", "name", "state_code", "top_prize", "overall_odds_one_in", "jackpot_odds_one_in"}
    if sort_by not in allowed_sorts:
        sort_by = "return_pct"

    cache_key = (state, min_price, max_price, min_return, sort_by, limit)
    if cache_key in _games_cache:
        return _games_cache[cache_key]

    conditions = ["g.is_active = TRUE", "g.ev IS NOT NULL"]
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
               g.jackpot_odds_one_in
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
               pt.prize_amount, pt.odds_one_in, pt.prizes_total, pt.prizes_remaining
        FROM games g
        LEFT JOIN prize_tiers pt ON pt.game_db_id = g.id
        WHERE g.id = $1
        ORDER BY pt.prize_amount DESC
    """, game_db_id)
    return [tuple(r) for r in rows]


async def get_states_summary(conn):
    rows = await conn.fetch("""
        SELECT state_code, state_name,
               COUNT(*) as game_count,
               MAX(scraped_at) as last_scraped,
               AVG(return_pct) as avg_return,
               MAX(return_pct) as best_return
        FROM games
        WHERE is_active=TRUE
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
                                notes: str = None, reported_at=None):
    if isinstance(reported_at, str):
        try:
            reported_at = dt.datetime.fromisoformat(reported_at.replace("Z", "+00:00"))
        except ValueError:
            reported_at = None
    await conn.execute("""
        INSERT INTO inventory_reports
        (retailer_id, retailer_name, retailer_city, lat, lng,
         game_name, game_price, has_stock, source, reporter_ip, reporter_username, notes, reported_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, COALESCE($13, NOW()))
    """, retailer_id, retailer_name, retailer_city, lat, lng,
         game_name, game_price, has_stock, source, reporter_ip, reporter_username, notes, reported_at)


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
    return [dict(zip(cols, r)) for r in rows]
