"""Run the NY scraper and dump the for-life games to verify the fix."""
import logging
logging.basicConfig(level=logging.WARNING)

from backend.scraper.states.new_york import NewYorkScraper

games = NewYorkScraper().scrape()
print(f"Total games: {len(games)}\n")

for g in games:
    name = g["name"].upper()
    if "LIFE" in name or "WEEK" in name:
        print(f"=== {g['name']} (${g['price']:.0f}) ===")
        print(f"  return_pct: {g['return_pct']}")
        print(f"  ev: {g['ev']}")
        print(f"  top_prize: {g['top_prize']}")
        print(f"  top_prize_remaining: {g['top_prize_remaining']}")
        print(f"  top_prize_is_annuity: {g['top_prize_is_annuity']}")
        print(f"  top_prize_cash_value: {g['top_prize_cash_value']}")
        print(f"  top_prize_annuity_years: {g['top_prize_annuity_years']}")
        print(f"  top_prize_annuity_annual: {g['top_prize_annuity_annual']}")
        print(f"  prize_pool_left: {g['prize_pool_left']}")
        print(f"  tickets_remaining: {g['tickets_remaining']}")
        print(f"  total_tickets: {g['total_tickets']}")
        for t in g["tiers"]:
            ann = " [ANNUITY]" if t.get("is_annuity") else ""
            cv = f" cash=${t['cash_value']:,.0f}" if t.get("cash_value") else ""
            print(f"    ${t['prize_amount']:>10,.0f}  1 in {t.get('odds_one_in') or 0:>14,.2f}  "
                  f"rem={t.get('prizes_remaining'):>10}{ann}{cv}")
        print()
