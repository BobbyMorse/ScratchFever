"""Confirm MA for-life games still work after find_top_prize change."""
import logging
logging.basicConfig(level=logging.WARNING)

from backend.scraper.states.massachusetts import MassachusettsScraper

games = MassachusettsScraper().scrape()
print(f"Total games: {len(games)}\n")

for g in games:
    name = g["name"].upper()
    if "LIFE" in name or "WEEK" in name:
        print(f"=== {g['name']} (${g['price']:.0f}) ===")
        print(f"  return_pct: {g['return_pct']}  ev: {g['ev']}")
        print(f"  top_prize: {g['top_prize']}  remaining: {g['top_prize_remaining']}")
        print(f"  is_annuity: {g['top_prize_is_annuity']}  cash_value: {g['top_prize_cash_value']}")
        print()
