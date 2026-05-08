import aiosqlite
import os

DB_PATH = (
    "/tmp/scratch_fever.db"
    if os.environ.get("VERCEL")
    else os.path.join(os.path.dirname(__file__), "..", "scratch_fever.db")
)


async def get_db():
    return await aiosqlite.connect(DB_PATH)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                is_active INTEGER DEFAULT 1,
                detail_url TEXT,
                image_url TEXT,
                scraped_at TEXT DEFAULT (datetime('now')),
                UNIQUE(state_code, game_id)
            );

            CREATE TABLE IF NOT EXISTS prize_tiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_db_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                prize_amount REAL NOT NULL,
                odds_one_in REAL,
                prizes_total INTEGER,
                prizes_remaining INTEGER
            );

            ALTER TABLE games ADD COLUMN IF NOT EXISTS prize_pool_left REAL;

            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_code TEXT NOT NULL,
                success INTEGER NOT NULL,
                games_scraped INTEGER DEFAULT 0,
                error_msg TEXT,
                ran_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS inventory_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                retailer_id TEXT NOT NULL,
                retailer_name TEXT,
                retailer_city TEXT,
                lat REAL,
                lng REAL,
                game_name TEXT,
                game_price REAL,
                has_stock INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'community',
                reporter_ip TEXT,
                reporter_username TEXT,
                notes TEXT,
                reported_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_games_state ON games(state_code);
            CREATE INDEX IF NOT EXISTS idx_games_return ON games(return_pct DESC);
            CREATE INDEX IF NOT EXISTS idx_games_price ON games(price);
            CREATE INDEX IF NOT EXISTS idx_tiers_game ON prize_tiers(game_db_id);
            CREATE INDEX IF NOT EXISTS idx_ir_retailer ON inventory_reports(retailer_id);
            CREATE INDEX IF NOT EXISTS idx_ir_reported ON inventory_reports(reported_at DESC);
        """)
        # Migrations: add columns that may not exist in older DBs
        for migration in [
            "ALTER TABLE games ADD COLUMN image_url TEXT",
            "ALTER TABLE inventory_reports ADD COLUMN reporter_username TEXT",
        ]:
            try:
                await db.execute(migration)
                await db.commit()
            except Exception:
                pass
        await db.commit()


async def upsert_game(db, state_code: str, state_name: str, game_id: str, game_data: dict) -> int:
    await db.execute("""
        INSERT INTO games (state_code, state_name, game_id, name, price, ev, return_pct,
            overall_odds_one_in, top_prize, top_prize_remaining,
            total_tickets, tickets_remaining, prize_pool_left, detail_url, image_url, scraped_at, is_active)
        VALUES (:state_code, :state_name, :game_id, :name, :price, :ev, :return_pct,
            :overall_odds_one_in, :top_prize, :top_prize_remaining,
            :total_tickets, :tickets_remaining, :prize_pool_left, :detail_url, :image_url, datetime('now'), 1)
        ON CONFLICT(state_code, game_id) DO UPDATE SET
            name=excluded.name,
            price=excluded.price,
            ev=excluded.ev,
            return_pct=excluded.return_pct,
            overall_odds_one_in=excluded.overall_odds_one_in,
            top_prize=excluded.top_prize,
            top_prize_remaining=excluded.top_prize_remaining,
            total_tickets=excluded.total_tickets,
            tickets_remaining=excluded.tickets_remaining,
            prize_pool_left=excluded.prize_pool_left,
            detail_url=excluded.detail_url,
            image_url=excluded.image_url,
            scraped_at=datetime('now'),
            is_active=1
    """, {
        "state_code": state_code,
        "state_name": state_name,
        "game_id": game_id,
        "image_url": None,
        "prize_pool_left": None,
        **game_data,
    })
    cursor = await db.execute(
        "SELECT id FROM games WHERE state_code=? AND game_id=?", (state_code, game_id)
    )
    row = await cursor.fetchone()
    return row[0]


async def upsert_prize_tiers(db, game_db_id: int, tiers: list[dict]):
    await db.execute("DELETE FROM prize_tiers WHERE game_db_id=?", (game_db_id,))
    for tier in tiers:
        await db.execute("""
            INSERT INTO prize_tiers (game_db_id, prize_amount, odds_one_in, prizes_total, prizes_remaining)
            VALUES (?, ?, ?, ?, ?)
        """, (
            game_db_id,
            tier.get("prize_amount"),
            tier.get("odds_one_in"),
            tier.get("prizes_total"),
            tier.get("prizes_remaining"),
        ))


async def get_all_games(db, state: str = None, min_price: float = None,
                        max_price: float = None, min_return: float = None,
                        sort_by: str = "return_pct", limit: int = 500):
    allowed_sorts = {"return_pct", "ev", "price", "name", "state_code", "top_prize", "overall_odds_one_in"}
    if sort_by not in allowed_sorts:
        sort_by = "return_pct"

    conditions = ["g.is_active = 1", "g.ev IS NOT NULL"]
    params = []

    if state:
        conditions.append("g.state_code = ?")
        params.append(state.upper())
    if min_price is not None:
        conditions.append("g.price >= ?")
        params.append(min_price)
    if max_price is not None:
        conditions.append("g.price <= ?")
        params.append(max_price)
    if min_return is not None:
        conditions.append("g.return_pct >= ?")
        params.append(min_return)

    where = " AND ".join(conditions)
    direction = "ASC" if sort_by in ("price", "name") else "DESC"
    query = f"""
        SELECT g.id, g.state_code, g.state_name, g.game_id, g.name, g.price,
               g.ev, g.return_pct, g.overall_odds_one_in,
               g.top_prize, g.top_prize_remaining,
               g.total_tickets, g.tickets_remaining,
               g.detail_url, g.image_url, g.scraped_at,
               COALESCE(g.prize_pool_left, (SELECT SUM(pt.prize_amount * pt.prizes_remaining)
                FROM prize_tiers pt WHERE pt.game_db_id = g.id)) AS prize_pool_remaining
        FROM games g
        WHERE {where}
        ORDER BY g.{sort_by} {direction}
        LIMIT ?
    """
    params.append(limit)
    cursor = await db.execute(query, params)
    return await cursor.fetchall()


async def get_game_detail(db, game_db_id: int):
    cursor = await db.execute("""
        SELECT g.id, g.state_code, g.state_name, g.game_id, g.name, g.price,
               g.ev, g.return_pct, g.overall_odds_one_in,
               g.top_prize, g.top_prize_remaining, g.total_tickets, g.tickets_remaining,
               g.is_active, g.detail_url, g.image_url, g.scraped_at,
               pt.prize_amount, pt.odds_one_in, pt.prizes_total, pt.prizes_remaining
        FROM games g
        LEFT JOIN prize_tiers pt ON pt.game_db_id = g.id
        WHERE g.id = ?
        ORDER BY pt.prize_amount DESC
    """, (game_db_id,))
    return await cursor.fetchall()


async def get_states_summary(db):
    cursor = await db.execute("""
        SELECT state_code, state_name,
               COUNT(*) as game_count,
               MAX(scraped_at) as last_scraped,
               AVG(return_pct) as avg_return,
               MAX(return_pct) as best_return
        FROM games
        WHERE is_active=1
        GROUP BY state_code, state_name
        ORDER BY state_name
    """)
    return await cursor.fetchall()


async def log_scrape(db, state_code: str, success: bool, games_scraped: int = 0, error_msg: str = None):
    await db.execute("""
        INSERT INTO scrape_log (state_code, success, games_scraped, error_msg)
        VALUES (?, ?, ?, ?)
    """, (state_code, 1 if success else 0, games_scraped, error_msg))


async def add_inventory_report(db, retailer_id: str, retailer_name: str = None,
                                retailer_city: str = None, lat: float = None, lng: float = None,
                                game_name: str = None, game_price: float = None,
                                has_stock: bool = False, source: str = "community",
                                reporter_ip: str = None, reporter_username: str = None,
                                notes: str = None, reported_at: str = None):
    await db.execute("""
        INSERT INTO inventory_reports
        (retailer_id, retailer_name, retailer_city, lat, lng,
         game_name, game_price, has_stock, source, reporter_ip, reporter_username, notes, reported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
    """, (retailer_id, retailer_name, retailer_city, lat, lng,
          game_name, game_price, 1 if has_stock else 0, source, reporter_ip, reporter_username, notes, reported_at))
    await db.commit()


async def get_recent_inventory_reports(db, limit: int = 200,
                                        retailer_id: str = None, game_name: str = None):
    conditions = []
    params = []
    if retailer_id:
        conditions.append("retailer_id = ?")
        params.append(retailer_id)
    if game_name:
        conditions.append("game_name LIKE ?")
        params.append(f"%{game_name}%")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    cursor = await db.execute(f"""
        SELECT id, retailer_id, retailer_name, retailer_city, lat, lng,
               game_name, game_price, has_stock, source, reporter_username, notes, reported_at
        FROM inventory_reports
        {where}
        ORDER BY reported_at DESC
        LIMIT ?
    """, params)
    cols = ["id", "retailer_id", "retailer_name", "retailer_city", "lat", "lng",
            "game_name", "game_price", "has_stock", "source", "reporter_username", "notes", "reported_at"]
    return [dict(zip(cols, row)) for row in await cursor.fetchall()]
