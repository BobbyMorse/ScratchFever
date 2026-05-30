import json, urllib.request

req = urllib.request.Request(
    "https://apim-website-prod-eastus.azure-api.net/scratchgamesapp/getscratchinfo",
    headers={"x-partner": "web", "Referer": "https://floridalottery.com/", "User-Agent": "Mozilla/5.0"},
)
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.load(r)

games = data.get("games", []) if isinstance(data, dict) else data
print(f"games: {len(games)}\n")

for g in games:
    name = (g.get("GameName") or "").upper()
    if "LIFE" in name or "WEEK" in name or "MONTH" in name:
        print(f"=== {g.get('GameName')!r} | price={g.get('TicketPrice')} | odds={g.get('OverallOdds')} ===")
        for t in g.get("OddsTiers") or []:
            print(f"   PrizeAmount={t.get('PrizeAmount')!r:>40}  odds={t.get('WinningOdds')}  total={t.get('TotalPrizes')}  rem={t.get('PrizesRemaining')}")
        # dump available fields on first tier
        if g.get("OddsTiers"):
            print(f"   tier0 keys: {list(g['OddsTiers'][0].keys())}")
        print()
