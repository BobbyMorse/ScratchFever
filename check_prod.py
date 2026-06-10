import datetime
import json
import sys
import urllib.request

PUSH_TIME = 1781049929  # 2026-06-09 20:05:29 EDT — when scraper fixes pushed
WATCH = {"MN", "NE", "OK", "OH", "WV", "OR", "NJ"}

try:
    req = urllib.request.Request("https://scratchfever.app/api/status/states")
    with urllib.request.urlopen(req, timeout=30) as resp:
        d = json.loads(resp.read())
except Exception as e:
    print(f"FETCH ERROR: {e}")
    sys.exit(1)

done = 0
rows = []
for r in d.get("states", []):
    if r["state_code"] not in WATCH:
        continue
    ls = r.get("last_scrape_at") or ""
    g = r.get("games_in_db", 0)
    ev = r.get("ev_pct") or 0
    wc = r.get("winners_count") or 0
    try:
        ts = datetime.datetime.fromisoformat(ls.replace("Z", "+00:00")).timestamp()
    except Exception:
        ts = 0
    fresh = ts > PUSH_TIME
    if fresh:
        done += 1
    rows.append((r["state_code"], g, ev, wc, fresh, ls[:19]))

print(f"{done}/{len(WATCH)} states scraped since push")
for c, g, e, w, f, t in sorted(rows):
    flag = "FRESH" if f else "stale"
    print(f"  {c}: games={g:3} ev={e:3}% wins={w:5}  [{flag}] last={t}")
if done >= len(WATCH):
    print("DONE")
