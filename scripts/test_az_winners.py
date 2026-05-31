"""Smoke test the AZ winners scraper: hit live page, parse, print summary."""
from __future__ import annotations
import sys
import io
import logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from backend.scraper.winners.arizona import ArizonaWinnersScraper

s = ArizonaWinnersScraper()
wins, err = s.safe_scrape(days=14)
if err:
    print(f"ERROR: {err}")
    sys.exit(1)

print(f"\n=== {len(wins)} wins (>= $10k, scratchers, not draws) ===\n")
for w in wins[:15]:
    print(f"  ${int(w['prize_amount']):>9,d}  {w['claim_date']}  "
          f"{(w['winner_city'] or 'N/A'):<20s}  {w['source_game_name']}")

print(f"\n... ({len(wins)} total)\n")

# Sanity: any obvious draw-game leaks?
draws = [w for w in wins if any(
    p in w["source_game_name"].lower()
    for p in ("powerball", "mega millions", "pick", "fantasy 5", "fastplay"))]
print(f"Draw-game leaks: {len(draws)} (should be 0)")
for w in draws[:5]:
    print(f"  LEAK: ${int(w['prize_amount']):,d} {w['source_game_name']}")

# Sanity: prize floor
sub = [w for w in wins if w["prize_amount"] < 10000]
print(f"Sub-$10k leaks: {len(sub)} (should be 0)")

# Sanity: source_id stability — re-scrape and compare? Skip for time.
# Just check uniqueness.
ids = [w["source_id"] for w in wins]
print(f"source_id uniqueness: {len(ids)} ids, {len(set(ids))} unique")
