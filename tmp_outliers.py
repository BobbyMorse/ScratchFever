"""Pull live API, list any game with return_pct > 100 or other red flags."""
import json, urllib.request

req = urllib.request.Request("https://scratchfever.app/api/games", headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.load(r)

games = data["games"] if isinstance(data, dict) else data
print(f"Total games: {len(games)}\n")

flagged = []
for g in games:
    rp = g.get("return_pct")
    price = g.get("price") or 0
    ev = g.get("ev")
    ppl = g.get("prize_pool_left")
    tr = g.get("tickets_remaining")
    total = g.get("total_tickets")
    face_outstanding = (tr or 0) * price

    reasons = []
    if rp is not None and rp > 100:
        reasons.append(f"return_pct={rp}")
    if ev is not None and price > 0 and ev > price:
        reasons.append(f"ev={ev} > price={price}")
    if ppl and tr and price > 0 and face_outstanding > 0 and ppl > face_outstanding * 3:
        reasons.append(f"prize_pool_left={ppl:.0f} > 3x face_outstanding={face_outstanding:.0f}")
    if tr is not None and total is not None and total > 0 and tr > total:
        reasons.append(f"tr={tr} > total={total}")

    if reasons:
        flagged.append((g, reasons))

print(f"Flagged: {len(flagged)}\n")
for g, reasons in sorted(flagged, key=lambda x: -(x[0].get("return_pct") or 0)):
    print(f"[{g.get('state_code')}] {g.get('name')!r} (${g.get('price')})")
    for r in reasons:
        print(f"    {r}")
    print(f"    top_prize={g.get('top_prize')} is_annuity={g.get('top_prize_is_annuity')} cash_value={g.get('top_prize_cash_value')}")
    print()
