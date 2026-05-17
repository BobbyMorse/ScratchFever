import asyncio
from dotenv import load_dotenv
load_dotenv()
from backend.database import init_db, get_pool, upsert_game

async def test():
    await init_db()
    pool = get_pool()

    # Simulate one FL game
    game_data = {
        'game_id': 'test_fl_9999',
        'name': 'Test Game FL',
        'price': 20.0,
        'ev': -4.22,
        'return_pct': 78.9,
        'overall_odds_one_in': 2.79,
        'top_prize': 5000000.0,
        'top_prize_remaining': 2,
        'jackpot_odds_one_in': 3068721.0,
        'total_tickets': 12237077,
        'tickets_remaining': 6137442,
        'detail_url': 'https://floridalottery.com/games/scratch-offs/view?id=9999',
        'image_url': None,
        'end_date': '2034-07-14',
    }

    async with pool.acquire() as conn:
        try:
            db_id = await upsert_game(conn, 'FL', 'Florida', 'test_fl_9999', game_data)
            print(f'SUCCESS: game_db_id={db_id}')
            # Clean up
            await conn.execute("DELETE FROM games WHERE game_id='test_fl_9999' AND state_code='FL'")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'FAILED: {e}')

asyncio.run(test())
