"""
One-shot audit: find games where stored overall_odds_one_in disagrees with
the value implied by prize-tier data (total_tickets / sum(prizes_total)).

Same inconsistency that flagged the RI bug — flags the next candidates.
"""
import asyncio, os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

SQL = """
WITH per_game AS (
  SELECT g.state_code, g.name, g.game_id,
         g.overall_odds_one_in AS stated,
         g.total_tickets,
         SUM(t.prizes_total) AS sum_winners
  FROM games g
  JOIN prize_tiers t ON t.game_db_id = g.id
  WHERE g.is_active = TRUE
    AND g.total_tickets IS NOT NULL
    AND g.overall_odds_one_in IS NOT NULL
  GROUP BY g.id
)
SELECT state_code,
       COUNT(*) AS n_games,
       AVG(stated) AS avg_stated,
       AVG(total_tickets::float / sum_winners) AS avg_computed,
       AVG(ABS(stated - total_tickets::float / sum_winners)) AS avg_abs_diff,
       SUM(CASE WHEN ABS(stated - total_tickets::float / sum_winners) > 0.5 THEN 1 ELSE 0 END) AS n_off_by_half,
       SUM(CASE WHEN stated < 2.0 THEN 1 ELSE 0 END) AS n_implausibly_low
FROM per_game
GROUP BY state_code
ORDER BY avg_abs_diff DESC NULLS LAST;
"""

WORST_GAMES_SQL = """
WITH per_game AS (
  SELECT g.state_code, g.name, g.game_id,
         g.overall_odds_one_in AS stated,
         g.total_tickets,
         SUM(t.prizes_total) AS sum_winners
  FROM games g
  JOIN prize_tiers t ON t.game_db_id = g.id
  WHERE g.is_active = TRUE
    AND g.total_tickets IS NOT NULL
    AND g.overall_odds_one_in IS NOT NULL
  GROUP BY g.id
)
SELECT state_code, name, stated,
       ROUND((total_tickets::float / sum_winners)::numeric, 2) AS computed,
       total_tickets, sum_winners
FROM per_game
WHERE ABS(stated - total_tickets::float / sum_winners) > 0.5
ORDER BY ABS(stated - total_tickets::float / sum_winners) DESC
LIMIT 25;
"""


async def main():
    url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        print("=" * 100)
        print(f"{'STATE':<6}{'N':>5}{'AVG STATED':>12}{'AVG COMPUTED':>14}{'AVG |DIFF|':>12}{'N OFF >0.5':>12}{'N <2.0':>10}")
        print("-" * 100)
        rows = await conn.fetch(SQL)
        def f(x, w, p=2):
            return f"{x:>{w}.{p}f}" if x is not None else f"{'-':>{w}}"
        for r in rows:
            print(f"{r['state_code']:<6}"
                  f"{r['n_games']:>5}"
                  f"{f(r['avg_stated'], 12)}"
                  f"{f(r['avg_computed'], 14)}"
                  f"{f(r['avg_abs_diff'], 12)}"
                  f"{r['n_off_by_half']:>12}"
                  f"{r['n_implausibly_low']:>10}")
        print("\nWORST INDIVIDUAL GAMES (stated vs computed):")
        print("-" * 100)
        worst = await conn.fetch(WORST_GAMES_SQL)
        for r in worst:
            print(f"  {r['state_code']:<4} {r['name'][:40]:<40} stated={r['stated']:>6.2f}  computed={r['computed']:>6.2f}  "
                  f"total_tickets={r['total_tickets']:>10}  sum_winners={r['sum_winners']:>10}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
