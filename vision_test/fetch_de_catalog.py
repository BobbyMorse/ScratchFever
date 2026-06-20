"""One-off: pull the live DE active-game catalog from the production DB."""
import asyncio, json, os
from pathlib import Path
import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0, timeout=10)
    rows = await conn.fetch(
        "SELECT id, game_id, name, price, top_prize, top_prize_remaining "
        "FROM games WHERE state_code=$1 AND is_active=TRUE "
        "ORDER BY price, name",
        "DE",
    )
    catalog = [dict(r) for r in rows]
    out = Path(__file__).resolve().parent / "de_catalog.json"
    out.write_text(json.dumps(catalog, indent=2, default=str))
    print(f"Wrote {len(catalog)} DE games to {out}")
    for r in catalog:
        print(f"  id={r['id']:>5}  {r['game_id']:>10}  ${r['price']:>5.0f}  {r['name']}")
    await conn.close()

asyncio.run(main())
