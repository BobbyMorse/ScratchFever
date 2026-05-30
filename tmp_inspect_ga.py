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
print(f"games: {len(games)}\n")

for g in games:
    name = (g.get("gameName") or "").upper()
    if "LIFE" in name or "MONTH" in name or "WEEK" in name or "YEAR" in name:
        if g.get("validationStatus") != "ACTIVE":
            continue
        print(f"=== {g.get('gameName')} (price={g.get('ticketPrice')}c, id={g.get('gameId')}) ===")
        for t in g.get("prizeTiers") or []:
            print(f"   prizeAmount={t.get('prizeAmount')} (={t.get('prizeAmount',0)/10000:.2f}) "
                  f"winning={t.get('winningTickets')} paid={t.get('paidTickets')} "
                  f"label={t.get('prizeLabel') or t.get('label') or t.get('description') or t.get('prizeText') or ''!r}")
        # dump all keys of first tier so we can see what fields exist
        if g.get("prizeTiers"):
            print(f"   tier0 keys: {list(g['prizeTiers'][0].keys())}")
        print()
