import re
import aiosqlite
from backend.database import DB_PATH

CALLER_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_name TEXT NOT NULL,
    game_number TEXT,
    game_price REAL,
    status TEXT DEFAULT 'paused',
    target_tiers TEXT DEFAULT 'ELITE,GOOD',
    max_stores INTEGER DEFAULT 200,
    calls_made INTEGER DEFAULT 0,
    hits_found INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS call_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES call_campaigns(id),
    retailer_id TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    phone_e164 TEXT,
    address TEXT,
    city TEXT,
    score INTEGER DEFAULT 0,
    tier TEXT,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    call_sid TEXT,
    last_attempt_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    lat REAL,
    lng REAL,
    UNIQUE(campaign_id, retailer_id)
);

CREATE TABLE IF NOT EXISTS call_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES call_queue(id),
    campaign_id INTEGER NOT NULL REFERENCES call_campaigns(id),
    call_sid TEXT UNIQUE,
    called_at TEXT DEFAULT (datetime('now')),
    duration_sec INTEGER,
    outcome TEXT,
    has_game INTEGER,
    confidence REAL,
    can_order INTEGER,
    notes TEXT,
    transcript TEXT
);

CREATE TABLE IF NOT EXISTS manual_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    retailer_id TEXT NOT NULL,
    name TEXT,
    address TEXT,
    city TEXT,
    phone TEXT,
    lat REAL,
    lng REAL,
    has_inventory INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    checked_at TEXT DEFAULT (datetime('now')),
    UNIQUE(campaign_id, retailer_id)
);

CREATE INDEX IF NOT EXISTS idx_cq_pending ON call_queue(campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_cr_hits ON call_results(campaign_id, has_game);
CREATE INDEX IF NOT EXISTS idx_mc_campaign ON manual_checks(campaign_id);
"""


async def init_caller_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CALLER_SCHEMA)
        # Migrations for existing databases
        for col, coltype in [("lat", "REAL"), ("lng", "REAL")]:
            try:
                await db.execute(f"ALTER TABLE call_queue ADD COLUMN {col} {coltype}")
            except Exception:
                pass
        await db.commit()


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if f != 0.0 else None
    except (TypeError, ValueError):
        return None


def normalize_phone(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


async def create_campaign(game_name: str, game_number: str, game_price: float,
                          target_tiers: str = "ELITE,GOOD", max_stores: int = 200) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO call_campaigns (game_name, game_number, game_price, target_tiers, max_stores, status)
               VALUES (?, ?, ?, ?, ?, 'paused')""",
            (game_name, game_number or "", game_price or 0.0, target_tiers, max_stores),
        )
        await db.commit()
        return cursor.lastrowid


ALL_TIERS = {"ELITE", "GOOD", "CAUTION", "AVOID"}


async def populate_queue(campaign_id: int, target_tiers: list[str], max_stores: int):
    from backend.ma_scorer import load_and_score

    all_tiers = set(t.upper() for t in target_tiers)
    retailers = load_and_score()

    eligible = [
        r for r in retailers
        if (not all_tiers or all_tiers >= ALL_TIERS or r["tier"] in all_tiers)
        and r.get("phone")
    ]
    if max_stores and max_stores < len(eligible):
        eligible = eligible[:max_stores]

    async with aiosqlite.connect(DB_PATH) as db:
        inserted = 0
        for r in eligible:
            e164 = normalize_phone(r["phone"])
            if not e164:
                continue
            lat = _safe_float(r.get("latitude"))
            lng = _safe_float(r.get("longitude"))
            await db.execute(
                """INSERT OR IGNORE INTO call_queue
                   (campaign_id, retailer_id, name, phone, phone_e164, address, city, score, tier, lat, lng)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (campaign_id, r["id"], r["name"], r["phone"], e164,
                 r["address"], r["city"], r["score"], r["tier"], lat, lng),
            )
            inserted += 1
        await db.commit()
    return inserted


async def get_next_pending(campaign_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM call_queue
               WHERE campaign_id=? AND status='pending' AND attempts < 3
               ORDER BY score DESC, id ASC LIMIT 1""",
            (campaign_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def mark_calling(queue_id: int, call_sid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE call_queue SET status='calling', call_sid=?,
               attempts=attempts+1, last_attempt_at=datetime('now')
               WHERE id=?""",
            (call_sid, queue_id),
        )
        await db.commit()


async def update_campaign_status(campaign_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE call_campaigns SET status=? WHERE id=?", (status, campaign_id))
        await db.commit()


async def update_queue_status(queue_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE call_queue SET status=? WHERE id=?", (status, queue_id))
        await db.commit()


async def mark_queue_no_inventory(queue_id: int, notes: str = "") -> bool:
    """Manually mark a queued store as no inventory (creates a manual call_result)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT campaign_id, status FROM call_queue WHERE id=?", (queue_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        campaign_id, status = row
        if status not in ("pending", "failed", "voicemail"):
            return False
        await db.execute(
            """INSERT OR IGNORE INTO call_results
               (queue_id, campaign_id, call_sid, outcome, has_game, confidence, notes)
               VALUES (?, ?, ?, 'manual', 0, 1.0, ?)""",
            (queue_id, campaign_id, f"manual_{queue_id}", notes or "Manually marked no stock"),
        )
        await db.execute("UPDATE call_queue SET status='done' WHERE id=?", (queue_id,))
        await db.commit()
    return True


async def add_manual_check(campaign_id: int, retailer_id: str, name: str,
                           address: str, city: str, phone: str,
                           lat: float, lng: float, has_inventory: int,
                           notes: str = "") -> bool:
    """Record a manual inventory check for any retailer (queued or not)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO manual_checks
               (campaign_id, retailer_id, name, address, city, phone, lat, lng, has_inventory, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(campaign_id, retailer_id) DO UPDATE SET
               has_inventory=excluded.has_inventory, notes=excluded.notes,
               checked_at=datetime('now')""",
            (campaign_id, retailer_id, name, address, city, phone, lat, lng, has_inventory, notes),
        )
        await db.commit()
    return True


async def get_manual_checks(campaign_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM manual_checks WHERE campaign_id=? ORDER BY checked_at DESC",
            (campaign_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def mark_no_inventory(result_id: int):
    """Flip a call_result hit to has_game=0 and decrement campaign hits_found."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT campaign_id, has_game FROM call_results WHERE id=?", (result_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        campaign_id, was_hit = row
        await db.execute("UPDATE call_results SET has_game=0 WHERE id=?", (result_id,))
        if was_hit == 1:
            await db.execute(
                "UPDATE call_campaigns SET hits_found=MAX(0, hits_found-1) WHERE id=?",
                (campaign_id,),
            )
        await db.commit()
    return True


async def mark_dnc(queue_id: int):
    """Mark a store as do-not-call across ALL campaigns by phone number."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT phone_e164 FROM call_queue WHERE id=?", (queue_id,))
        row = await cursor.fetchone()
        if row and row[0]:
            await db.execute(
                "UPDATE call_queue SET status='dnc' WHERE phone_e164=? AND status='pending'",
                (row[0],),
            )
        await db.execute("UPDATE call_queue SET status='dnc' WHERE id=?", (queue_id,))
        await db.commit()


async def save_result(queue_id: int, campaign_id: int, call_sid: str,
                      outcome: str, has_game, confidence: float,
                      can_order, notes: str, transcript: str,
                      duration_sec: int = None):
    has_game_int = 1 if has_game is True else (0 if has_game is False else None)
    can_order_int = 1 if can_order is True else (0 if can_order is False else None)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO call_results
               (queue_id, campaign_id, call_sid, outcome, has_game, confidence,
                can_order, notes, transcript, duration_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (queue_id, campaign_id, call_sid, outcome, has_game_int,
             confidence, can_order_int, notes, transcript, duration_sec),
        )
        if has_game:
            await db.execute(
                "UPDATE call_campaigns SET hits_found=hits_found+1 WHERE id=?",
                (campaign_id,),
            )
        await db.execute(
            "UPDATE call_campaigns SET calls_made=calls_made+1 WHERE id=?",
            (campaign_id,),
        )
        # Mirror conclusive results into the shared inventory_reports table
        if has_game_int is not None:
            cursor = await db.execute(
                "SELECT retailer_id, name, city, lat, lng FROM call_queue WHERE id=?",
                (queue_id,),
            )
            q_row = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT game_name, game_price FROM call_campaigns WHERE id=?",
                (campaign_id,),
            )
            c_row = await cursor.fetchone()
            if q_row and c_row:
                await db.execute("""
                    INSERT INTO inventory_reports
                    (retailer_id, retailer_name, retailer_city, lat, lng,
                     game_name, game_price, has_stock, source, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'caller', ?)
                """, (q_row[0], q_row[1], q_row[2], q_row[3], q_row[4],
                      c_row[0], c_row[1], has_game_int, notes))
        await db.commit()


async def get_campaign(campaign_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM call_campaigns WHERE id=?", (campaign_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def list_campaigns() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM call_campaigns ORDER BY created_at DESC")
        return [dict(r) for r in await cursor.fetchall()]


async def get_queue_stats(campaign_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM call_queue WHERE campaign_id=? GROUP BY status",
            (campaign_id,),
        )
        return dict(await cursor.fetchall())


async def get_hits(campaign_id: int = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if campaign_id:
            cursor = await db.execute(
                """SELECT cr.*, cq.name, cq.address, cq.city, cq.phone
                   FROM call_results cr
                   JOIN call_queue cq ON cq.id = cr.queue_id
                   WHERE cr.campaign_id=? AND cr.has_game=1
                   ORDER BY cr.confidence DESC""",
                (campaign_id,),
            )
        else:
            cursor = await db.execute(
                """SELECT cr.*, cq.name, cq.address, cq.city, cq.phone,
                          cc.game_name, cc.game_price
                   FROM call_results cr
                   JOIN call_queue cq ON cq.id = cr.queue_id
                   JOIN call_campaigns cc ON cc.id = cr.campaign_id
                   WHERE cr.has_game=1
                   ORDER BY cr.called_at DESC"""
            )
        return [dict(r) for r in await cursor.fetchall()]


async def get_queue(campaign_id: int, status: str = None,
                    limit: int = 100, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cursor = await db.execute(
                """SELECT * FROM call_queue WHERE campaign_id=? AND status=?
                   ORDER BY score DESC, id ASC LIMIT ? OFFSET ?""",
                (campaign_id, status, limit, offset),
            )
        else:
            cursor = await db.execute(
                """SELECT * FROM call_queue WHERE campaign_id=?
                   ORDER BY score DESC, id ASC LIMIT ? OFFSET ?""",
                (campaign_id, limit, offset),
            )
        return [dict(r) for r in await cursor.fetchall()]


async def get_recent_results(campaign_id: int, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT cr.*, cq.name, cq.address, cq.city, cq.phone
               FROM call_results cr
               JOIN call_queue cq ON cq.id = cr.queue_id
               WHERE cr.campaign_id=?
               ORDER BY cr.called_at DESC LIMIT ?""",
            (campaign_id, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]
