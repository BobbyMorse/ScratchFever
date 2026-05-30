"""Regression test: MA, NY, GA for-life games should all produce sane EVs."""
import logging
logging.basicConfig(level=logging.WARNING)

from backend.scraper.states.massachusetts import MassachusettsScraper
from backend.scraper.states.new_york import NewYorkScraper
from backend.scraper.states.georgia import GeorgiaScraper

print("=" * 70)
print("MA for-life games (heuristic-applied, NOT pre-marked by scraper)")
print("=" * 70)
for g in MassachusettsScraper().scrape():
    n = (g["name"] or "").upper()
    if "LIFE" in n or "WEEK" in n:
        print(f"  {g['name']!r}  ret={g['return_pct']}%  top=${g['top_prize']}  ann={g['top_prize_is_annuity']}  cash=${g['top_prize_cash_value']}")

print()
print("=" * 70)
print("NY for-life games (pre-marked by scraper)")
print("=" * 70)
for g in NewYorkScraper().scrape():
    n = (g["name"] or "").upper()
    if "LIFE" in n or "WEEK" in n:
        print(f"  {g['name']!r}  ret={g['return_pct']}%  top=${g['top_prize']}  ann={g['top_prize_is_annuity']}  cash=${g['top_prize_cash_value']}")

print()
print("=" * 70)
print("GA for-life games (pre-marked by scraper, zero-tier reconstruction)")
print("=" * 70)
for g in GeorgiaScraper().scrape():
    n = (g["name"] or "").upper()
    if "LIFE" in n or "WEEK" in n or "MONTH" in n:
        print(f"  {g['name']!r}  ret={g['return_pct']}%  top=${g['top_prize']}  ann={g['top_prize_is_annuity']}  cash=${g['top_prize_cash_value']}")
