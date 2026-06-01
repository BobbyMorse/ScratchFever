"""Quick check: does the existing WA BLOCK_RE match anything in the saved probe HTML?"""
from __future__ import annotations
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

with open("scripts/_winners_probe_walottery_com.html", encoding="utf-8") as f:
    html = f.read()
print(f"file size: {len(html):,} bytes")

from backend.scraper.winners.washington import BLOCK_RE
matches = list(BLOCK_RE.finditer(html))
print(f"BLOCK_RE matches: {len(matches)}")

if matches:
    for i, m in enumerate(matches[:3]):
        print(f"\n--- match {i} ---")
        for j, g in enumerate(m.groups(), 1):
            print(f"  [{j}] {(g or '-')[:80]}")
else:
    # Try a few simpler subpatterns to localize the failure
    import re
    print("\nTrying simpler patterns to localize:")
    for name, pat in [
        ("date row", r"<tr>\s*<td><strong>(\w+\s+\d+,\s*\d{4})</strong></td>"),
        ("NAME row", r"<tr>\s*<td><strong>NAME:</strong>\s*([^<]+?)</td>"),
        ("prize after name", r"<td><strong>NAME:</strong>[^<]+</td>\s*<td>\$([\d,.]+)</td>"),
        ("LOCATION row", r"<strong>LOCATION:</strong>\s*([^<]+?)<br[^>]*>\s*([^<]+?)</td>"),
    ]:
        cnt = len(re.findall(pat, html))
        print(f"  {name}: {cnt} matches")
