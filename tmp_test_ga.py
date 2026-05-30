import logging
logging.basicConfig(level=logging.WARNING)
from backend.scraper.states.georgia import GeorgiaScraper

games = GeorgiaScraper().scrape()
print(f"GA: {len(games)} games\n")
for g in games:
    n = (g["name"] or "").upper()
    if "LIFE" in n or "WEEK" in n or "MONTH" in n:
        print(f"=== {g['name']} (${g['price']:.0f}) ===")
        print(f"  return_pct: {g['return_pct']}  ev: {g['ev']}")
        print(f"  top_prize: {g['top_prize']}  rem: {g['top_prize_remaining']}")
        print(f"  is_annuity: {g['top_prize_is_annuity']}  cash_value: {g['top_prize_cash_value']}")
        print(f"  prize_pool_left: {g['prize_pool_left']}")
        for t in g["tiers"][:5]:
            ann = " [ANN]" if t.get("is_annuity") else ""
            print(f"    ${t['prize_amount']:>10,.0f}  total={t.get('prizes_total'):>8}  rem={t.get('prizes_remaining'):>8}{ann}")
        print()
