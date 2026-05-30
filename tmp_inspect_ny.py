import sys, json, urllib.request

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://nylottery.ny.gov/scratch-off-games"}

for p in range(6):
    url = f"https://nylottery.ny.gov/drupal-api/api/scratch_off_games?_format=json&page={p}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    for row in d.get("rows", []):
        title = (row.get("title") or "").upper()
        if "LIFE" in title or "WEEK" in title:
            print("---", row.get("title"), "| price:", row.get("ticket_price"), "| odds:", row.get("overall_odds"))
            for t in row.get("odds_prizes") or []:
                print("   prize:", t.get("prize_amount"), "odds:", t.get("overall_odds"),
                      "rem:", t.get("prizes_remaining"), "paid:", t.get("prizes_paid_out"))
