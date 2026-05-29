"""
One-shot audit: find games where stored overall_odds_one_in disagrees with
the value implied by prize-tier data (total_tickets / sum(prizes_total)).

Same inconsistency that flagged the RI bug — flags the next candidates.
"""
import asyncio, os
import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def main():
    url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        # Per-game outliers: implausibly low (<2.0) or high (>15.0) stated odds.
        print("=" * 100)
        print("GAMES WITH IMPLAUSIBLE STATED OVERALL_ODDS (<2.0 or >15.0):")
        print("-" * 100)
        rows = await conn.fetch("""
            SELECT state_code, name, game_id, overall_odds_one_in, price
            FROM games
            WHERE is_active = TRUE
              AND overall_odds_one_in IS NOT NULL
              AND (overall_odds_one_in < 2.0 OR overall_odds_one_in > 15.0)
            ORDER BY overall_odds_one_in ASC
            LIMIT 60;
        """)
        for r in rows:
            print(f"  {r['state_code']:<4} {r['name'][:45]:<45} odds=1 in {r['overall_odds_one_in']:>6.2f}  ${r['price']}")
        print(f"\nTotal: {len(rows)}")

        print("\n" + "=" * 100)
        print("PER-STATE COUNT OF IMPLAUSIBLY LOW (<2.5) STATED ODDS:")
        print("-" * 100)
        rows = await conn.fetch("""
            SELECT state_code,
                   COUNT(*) FILTER (WHERE overall_odds_one_in < 2.5) AS n_low,
                   COUNT(*) FILTER (WHERE overall_odds_one_in IS NOT NULL) AS n_total,
                   MIN(overall_odds_one_in) AS min_odds,
                   MAX(overall_odds_one_in) AS max_odds
            FROM games
            WHERE is_active = TRUE
            GROUP BY state_code
            HAVING COUNT(*) FILTER (WHERE overall_odds_one_in < 2.5) > 0
            ORDER BY n_low DESC;
        """)
        for r in rows:
            print(f"  {r['state_code']:<4} {r['n_low']:>4} of {r['n_total']:>4} games <2.5;  min={r['min_odds']:.2f}  max={r['max_odds']:.2f}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
