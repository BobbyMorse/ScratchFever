"""Check detail_url for every game in prod and report broken links by state."""
import asyncio
import json
import sys
from collections import defaultdict

import httpx

API = "https://scratchfrenzy.app/api/games?limit=5000"
TIMEOUT = 30
CONCURRENCY = 12
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Upgrade-Insecure-Requests": "1",
}


async def check(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> tuple[str, int | str]:
    async with sem:
        # GET only (HEAD triggers different bot-detection paths on many lottery sites).
        # Retry once on transient failure.
        for attempt in (1, 2):
            try:
                r = await client.get(
                    url, follow_redirects=True, timeout=TIMEOUT, headers=BROWSER_HEADERS,
                )
                return url, r.status_code
            except httpx.TimeoutException:
                if attempt == 2:
                    return url, "TIMEOUT"
            except httpx.RequestError as e:
                if attempt == 2:
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
