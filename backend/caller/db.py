from __future__ import annotations
import re
from backend.database import get_pool


async def init_caller_db():
    async with get_pool().acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS call_campaigns (
                id SERIAL PRIMARY KEY,
                game_name TEXT NOT NULL,
                game_number TEXT,
                game_price REAL,
                status TEXT DEFAULT 'paused',
                target_tiers TEXT DEFAULT 'ELITE,GOOD',
                max_stores INTEGER DEFAULT 200,
                calls_made INTEGER DEFAULT 0,
                hits_found INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS call_queue (
                id SERIAL PRIMARY KEY,
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
                last_attempt_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                lat REAL,
                lng REAL,
                UNIQUE(campaign_id, retailer_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS call_results (
                id SERIAL PRIMARY KEY,
                queue_id INTEGER NOT NULL REFERENCES call_queue(id),
                campaign_id INTEGER NOT NULL REFERENCES call_campaigns(id),
                call_sid TEXT UNIQUE,
                called_at TIMESTAMPTZ DEFAULT NOW(),
                duration_sec INTEGER,
                outcome TEXT,
                has_game BOOLEAN,
                confidence REAL,
                can_order BOOLEAN,
                notes TEXT,
                transcript TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_checks (
                id SERIAL PRIMARY KEY,
                campaign_id INTEGER NOT NULL,
                retailer_id TEXT NOT NULL,
                name TEXT,
                address TEXT,
                city TEXT,
                phone TEXT,
                lat REAL,
                lng REAL,
                has_inventory BOOLEAN NOT NULL DEFAULT FALSE,
                notes TEXT,
                checked_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(campaign_id, retailer_id)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_cq_pending ON call_queue(campaign_id, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_hits ON call_results(campaign_id, has_game)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_campaign ON manual_checks(campaign_id)")
        await conn.execute(
            "ALTER TABLE call_campaigns ADD COLUMN IF NOT EXISTS call_backend TEXT DEFAULT 'twilio_ivr'"
        )
        await conn.execute(
            "UPDATE call_campaigns SET call_backend='twilio_ivr' WHERE call_backend='bland'"
        )


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
                          target_tiers: str = "ELITE,GOOD", max_stores: int = 200,
                          call_backend: str = "twilio_ivr") -> int:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO call_campaigns
               (game_name, game_number, game_price, target_tiers, max_stores, status, call_backend)
               VALUES ($1, $2, $3, $4, $5, 'paused', $6) RETURNING id""",
            game_name, game_number or "", game_price or 0.0, target_tiers, max_stores, call_backend,
        )
        return row["id"]


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

    async with get_pool().acquire() as conn:
        inserted = 0
        for r in eligible:
            e164 = normalize_phone(r["phone"])
            if not e164:
                continue
            lat = _safe_float(r.get("latitude"))
            lng = _safe_float(r.get("longitude"))
            await conn.execute(
                """INSERT INTO call_queue
                   (campaign_id, retailer_id, name, phone, phone_e164, address, city, score, tier, lat, lng)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                   ON CONFLICT (campaign_id, retailer_id) DO NOTHING""",
                campaign_id, r["id"], r["name"], r["phone"], e164,
                r["address"], r["city"], r["score"], r["tier"], lat, lng,
            )
            inserted += 1
    return inserted


async def get_next_pending(campaign_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM call_queue
               WHERE campaign_id=$1 AND status='pending' AND attempts < 3
               ORDER BY score DESC, id ASC LIMIT 1""",
            campaign_id,
        )
        return dict(row) if row else None


async def mark_calling(queue_id: int, call_sid: str):
    async with get_pool().acquire() as conn:
        await conn.execute(
            """UPDATE call_queue SET status='calling', call_sid=$1,
               attempts=attempts+1, last_attempt_at=NOW() WHERE id=$2""",
            call_sid, queue_id,
        )


async def update_campaign_status(campaign_id: int, status: str):
    async with get_pool().acquire() as conn:
        await conn.execute("UPDATE call_campaigns SET status=$1 WHERE id=$2", status, campaign_id)


async def update_queue_status(queue_id: int, status: str):
    async with get_pool().acquire() as conn:
        await conn.execute("UPDATE call_queue SET status=$1 WHERE id=$2", status, queue_id)


async def mark_queue_no_inventory(queue_id: int, notes: str = "") -> bool:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT campaign_id, status FROM call_queue WHERE id=$1", queue_id
        )
        if not row or row["status"] not in ("pending", "failed", "voicemail"):
            return False
        await conn.execute(
            """INSERT INTO call_results (queue_id, campaign_id, call_sid, outcome, has_game, confidence, notes)
               VALUES ($1, $2, $3, 'manual', FALSE, 1.0, $4)
               ON CONFLICT (call_sid) DO NOTHING""",
            queue_id, row["campaign_id"], f"manual_{queue_id}", notes or "Manually marked no stock",
        )
        await conn.execute("UPDATE call_queue SET status='done' WHERE id=$1", queue_id)
    return True


async def add_manual_check(campaign_id: int, retailer_id: str, name: str,
                           address: str, city: str, phone: str,
                           lat: float, lng: float, has_inventory: int,
                           notes: str = "") -> bool:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO manual_checks
               (campaign_id, retailer_id, name, address, city, phone, lat, lng, has_inventory, notes)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT(campaign_id, retailer_id) DO UPDATE SET
               has_inventory=EXCLUDED.has_inventory, notes=EXCLUDED.notes, checked_at=NOW()""",
            campaign_id, retailer_id, name, address, city, phone, lat, lng, bool(has_inventory), notes,
        )
    return True


async def get_manual_checks(campaign_id: int) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM manual_checks WHERE campaign_id=$1 ORDER BY checked_at DESC",
            campaign_id,
        )
        return [dict(r) for r in rows]


async def mark_no_inventory(result_id: int):
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT campaign_id, has_game FROM call_results WHERE id=$1", result_id
        )
        if not row:
            return False
        await conn.execute("UPDATE call_results SET has_game=FALSE WHERE id=$1", result_id)
        if row["has_game"]:
            await conn.execute(
                "UPDATE call_campaigns SET hits_found=GREATEST(0, hits_found-1) WHERE id=$1",
                row["campaign_id"],
            )
    return True


async def mark_dnc(queue_id: int):
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT phone_e164 FROM call_queue WHERE id=$1", queue_id)
        if row and row["phone_e164"]:
            await conn.execute(
                "UPDATE call_queue SET status='dnc' WHERE phone_e164=$1 AND status='pending'",
                row["phone_e164"],
            )
        await conn.execute("UPDATE call_queue SET status='dnc' WHERE id=$1", queue_id)


async def save_result(queue_id: int, campaign_id: int, call_sid: str,
                      outcome: str, has_game, confidence: float,
                      can_order, notes: str, transcript: str,
                      duration_sec: int = None):
    has_game_bool = True if has_game is True else (False if has_game is False else None)
    can_order_bool = True if can_order is True else (False if can_order is False else None)

    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO call_results
                   (queue_id, campaign_id, call_sid, outcome, has_game, confidence,
                    can_order, notes, transcript, duration_sec)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   ON CONFLICT (call_sid) DO NOTHING""",
                queue_id, campaign_id, call_sid, outcome, has_game_bool,
                confidence, can_order_bool, notes, transcript, duration_sec,
            )
            if has_game_bool:
                await conn.execute(
                    "UPDATE call_campaigns SET hits_found=hits_found+1 WHERE id=$1", campaign_id
                )
            await conn.execute(
                "UPDATE call_campaigns SET calls_made=calls_made+1 WHERE id=$1", campaign_id
            )
            if has_game_bool is not None:
                q_row = await conn.fetchrow(
                    "SELECT retailer_id, name, city, lat, lng FROM call_queue WHERE id=$1", queue_id
                )
                c_row = await conn.fetchrow(
                    "SELECT game_name, game_price FROM call_campaigns WHERE id=$1", campaign_id
                )
                if q_row and c_row:
                    await conn.execute("""
                        INSERT INTO inventory_reports
                        (retailer_id, retailer_name, retailer_city, lat, lng,
                         game_name, game_price, has_stock, source, notes)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'caller', $9)
                    """, q_row["retailer_id"], q_row["name"], q_row["city"],
                         q_row["lat"], q_row["lng"], c_row["game_name"], c_row["game_price"],
                         has_game_bool, notes)


async def get_campaign(campaign_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM call_campaigns WHERE id=$1", campaign_id)
        return dict(row) if row else None


async def list_campaigns() -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM call_campaigns ORDER BY created_at DESC")
        return [dict(r) for r in rows]


async def get_queue_stats(campaign_id: int) -> dict:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) FROM call_queue WHERE campaign_id=$1 GROUP BY status",
            campaign_id,
        )
        return {r["status"]: r["count"] for r in rows}


async def get_hits(campaign_id: int = None) -> list[dict]:
    async with get_pool().acquire() as conn:
        if campaign_id:
            rows = await conn.fetch(
                """SELECT cr.*, cq.name, cq.address, cq.city, cq.phone
                   FROM call_results cr
                   JOIN call_queue cq ON cq.id = cr.queue_id
                   WHERE cr.campaign_id=$1 AND cr.has_game=TRUE
                   ORDER BY cr.confidence DESC""",
                campaign_id,
            )
        else:
            rows = await conn.fetch(
                """SELECT cr.*, cq.name, cq.address, cq.city, cq.phone,
                          cc.game_name, cc.game_price
                   FROM call_results cr
                   JOIN call_queue cq ON cq.id = cr.queue_id
                   JOIN call_campaigns cc ON cc.id = cr.campaign_id
                   WHERE cr.has_game=TRUE
                   ORDER BY cr.called_at DESC"""
            )
        return [dict(r) for r in rows]


async def get_queue(campaign_id: int, status: str = None,
                    limit: int = 100, offset: int = 0) -> list[dict]:
    async with get_pool().acquire() as conn:
        if status:
            rows = await conn.fetch(
                """SELECT * FROM call_queue WHERE campaign_id=$1 AND status=$2
                   ORDER BY score DESC, id ASC LIMIT $3 OFFSET $4""",
                campaign_id, status, limit, offset,
            )
        else:
            rows = await conn.fetch(
                """SELECT * FROM call_queue WHERE campaign_id=$1
                   ORDER BY score DESC, id ASC LIMIT $2 OFFSET $3""",
                campaign_id, limit, offset,
            )
        return [dict(r) for r in rows]


async def get_recent_results(campaign_id: int, limit: int = 50) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT cr.*, cq.name, cq.address, cq.city, cq.phone
               FROM call_results cr
               JOIN call_queue cq ON cq.id = cr.queue_id
               WHERE cr.campaign_id=$1
               ORDER BY cr.called_at DESC LIMIT $2""",
            campaign_id, limit,
        )
        return [dict(r) for r in rows]
