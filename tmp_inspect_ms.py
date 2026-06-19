import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
import requests
from bs4 import BeautifulSoup
from backend.scraper.states.mississippi import _parse_detail_soup, GAMES_URL, BASE_URL
from backend.scraper.base import HEADERS

resp = requests.get(GAMES_URL, headers=HEADERS, timeout=15)
soup = BeautifulSoup(resp.text, "lxml")
slugs = []
seen = set()
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "/instantgames/" not in href:
        continue
    slug = href.rstrip("/").split("/")[-1]
    if slug and slug != "instantgames" and slug not in seen:
        seen.add(slug)
        slugs.append((slug, (BASE_URL + href) if href.startswith("/") else href))

print(f"Found {len(slugs)} game links on listing")

import concurrent.futures as cf
def fetch(slug, url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        return slug, url, _parse_detail_soup(BeautifulSoup(r.text, "lxml"))
    except Exception as e:
        return slug, url, None

ok = 0
sample_print = 0
with cf.ThreadPoolExecutor(max_workers=8) as pool:
    futs = [pool.submit(fetch, s, u) for s, u in slugs]
    for f in cf.as_completed(futs):
        slug, url, parsed = f.result()
        if parsed:
            ok += 1
            if sample_print < 3:
                sample_print += 1
                print(f"  OK {slug}: name={parsed['name']!r} price={parsed['price']} tiers={len(parsed['tiers'])} odds={parsed['overall_odds']}")
        else:
            if sample_print < 6:
                print(f"  FAIL {slug}: {url}")
                sample_print += 1

print(f"\nParsed {ok}/{len(slugs)} pages")
