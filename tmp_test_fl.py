import logging
logging.basicConfig(level=logging.WARNING)
from backend.scraper.states.florida import FloridaScraper
from backend.scraper.states.new_york import NewYorkScraper

print("=" * 70)
print("FL for-life games")
print("=" * 70)
for g in FloridaScraper().scrape():
    n = (g["name"] or "").upper()
    if "LIFE" in n or "WEEK" in n or "YR" in n:
        print(f"  {g['name']!r}  ret={g['return_pct']}%  top=${g['top_prize']}  "
              f"ann={g['top_prize_is_annuity']}  cash=${g['top_prize_cash_value']}")
        for t in g["tiers"][:3]:
            ann = " [ANN]" if t.get("is_annuity") else ""
            print(f"     ${t['prize_amount']:>10,.0f}  total={t.get('prizes_total'):>6}  rem={t.get('prizes_remaining'):>6}{ann}")
        print()

print()
print("=" * 70)
print("NY for-life games (regression check after refactor)")
print("=" * 70)
for g in NewYorkScraper().scrape():
    n = (g["name"] or "").upper()
    if "LIFE" in n or "WEEK" in n:
        print(f"  {g['name']!r}  ret={g['return_pct']}%  top=${g['top_prize']}  "
              f"ann={g['top_prize_is_annuity']}  cash=${g['top_prize_cash_value']}")
