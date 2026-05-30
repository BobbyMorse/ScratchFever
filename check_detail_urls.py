"""Check detail_url for every game in prod and report broken links by state."""
import asyncio
import json
import sys
from collections import defaultdict

import httpx

API = "https://scratchfever.app/api/games?limit=5000"
TIMEOUT = 15
CONCURRENCY = 24
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


async def check(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> tuple[str, int | str]:
    async with sem:
        for method in ("HEAD", "GET"):
            try:
                r = await client.request(
                    method, url, follow_redirects=True, timeout=TIMEOUT,
                    headers={"User-Agent": UA, "Accept": "text/html,*/*"},
                )
                # Some servers reject HEAD with 4xx but serve GET fine, so retry on bad HEAD.
                if method == "HEAD" and r.status_code in (403, 405, 501):
                    continue
                return url, r.status_code
            except httpx.TimeoutException:
                return url, "TIMEOUT"
            except httpx.RequestError as e:
                return url, f"ERR:{type(e).__name__}"
        return url, "ERR:unreachable"


async def main():
    games = json.load(open("games.json"))["games"]
    url_to_games = defaultdict(list)
    for g in games:
        u = g.get("detail_url")
        if u:
            url_to_games[u].append((g["state_code"], g["name"]))
    urls = list(url_to_games.keys())
    print(f"checking {len(urls)} unique URLs across {len(games)} games", file=sys.stderr)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(http2=False, verify=False) as client:
        results = await asyncio.gather(*(check(client, u, sem) for u in urls))

    broken_by_state = defaultdict(list)
    ok_by_state = defaultdict(int)
    for url, code in results:
        ok = isinstance(code, int) and 200 <= code < 400
        for state, name in url_to_games[url]:
            if ok:
                ok_by_state[state] += 1
            else:
                broken_by_state[state].append((code, name, url))

    print("\n=== SUMMARY ===")
    all_states = sorted(set(ok_by_state) | set(broken_by_state))
    for s in all_states:
        ok = ok_by_state[s]
        bad = len(broken_by_state[s])
        flag = " *** BROKEN ***" if bad else ""
        print(f"{s}: ok={ok} broken={bad}{flag}")

    print("\n=== BROKEN URLS ===")
    for s in sorted(broken_by_state):
        print(f"\n--- {s} ({len(broken_by_state[s])} broken) ---")
        seen = set()
        for code, name, url in broken_by_state[s]:
            if url in seen:
                continue
            seen.add(url)
            print(f"  [{code}] {name}  ->  {url}")


asyncio.run(main())
