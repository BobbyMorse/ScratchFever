import json, urllib.request

req = urllib.request.Request(
    "https://www.galottery.com/api/v1/instant-games/games?size=1000&page=0",
    headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.galottery.com/en-us/games/scratchers/active-games.html",
        "Accept": "application/json",
    },
)
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.load(r)

games = data.get("games", []) if isinstance(data, dict) else data

# 1. Check name truncation
print("=== Long game names (>30 chars) ===")
for g in games:
    name = g.get("gameName") or ""
    if len(name) > 30 and g.get("validationStatus") == "ACTIVE":
        print(f"   ({len(name)}) {name!r}  keys={[k for k in g.keys() if 'name' in k.lower() or 'title' in k.lower()]}")

# 2. All ACTIVE games with prizeAmount==0 tiers
print("\n=== Active games with prizeAmount=0 tier (potential for-life) ===")
for g in games:
    if g.get("validationStatus") != "ACTIVE":
        continue
    zero_tiers = [t for t in (g.get("prizeTiers") or []) if (t.get("prizeAmount") or 0) == 0 and (t.get("winningTickets") or 0) > 0]
    if zero_tiers:
        name = g.get("gameName") or ""
        # try to find a longer-name field
        for k in g.keys():
            v = g[k]
            if isinstance(v, str) and "FOR LIFE" in v.upper() and len(v) > len(name):
                name = v
        print(f"   {g.get('gameName')!r}  price=${(g.get('ticketPrice') or 0)/100:.0f}  id={g.get('gameId')}")
        for zt in zero_tiers:
            print(f"     zero tier: winning={zt.get('winningTickets')} paid={zt.get('paidTickets')}")
        # also dump top non-zero tier
        nz = [(t.get("prizeAmount") or 0, t) for t in g.get("prizeTiers") or []]
        nz.sort(reverse=True, key=lambda x: x[0])
        if nz and nz[0][0] > 0:
            print(f"     top cash: ${nz[0][0]/10000:.0f}  winning={nz[0][1].get('winningTickets')} paid={nz[0][1].get('paidTickets')}")
        # all keys in first game
        if g == [x for x in games if x.get("gameName") == g.get("gameName")][0]:
            print(f"     game keys: {sorted(g.keys())}")

# 3. List ALL keys on a sample game
print("\n=== Sample game full structure ===")
sample = next((g for g in games if "FOR LIFE" in (g.get("gameName") or "").upper() and g.get("validationStatus") == "ACTIVE"), None)
if sample:
    print(json.dumps({k: v for k, v in sample.items() if k != "prizeTiers"}, indent=2, default=str)[:2000])
