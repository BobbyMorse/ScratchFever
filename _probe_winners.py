"""Probe the 3 timing-out winners scrapers to find the timeout cause."""
import requests
import re
import time
import sys

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---- AR
AR_ROW_RE = re.compile(
    r'<tr>\s*'
    r'<td data-cell-title="Name:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="City:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="Amount:\s*">\s*\$?([\d,.]+)\s*</td>\s*'
    r'<td data-cell-title="Game:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="Date Claimed:\s*">\s*([^<]*?)\s*</td>',
    re.IGNORECASE,
)
r = requests.get("https://www.myarkansaslottery.com/winners?page=1",
                 headers=HEADERS, timeout=30)
print("AR p1: status=%s, len=%d" % (r.status_code, len(r.text)))
matches = list(AR_ROW_RE.finditer(r.text))
print("AR regex matches: %d" % len(matches))

# Try a relaxed regex
AR_RELAXED = re.compile(
    r'<td data-cell-title="Name:[^>]*>\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="City:[^>]*>\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="Amount:[^>]*>\s*\$?([\d,.]+)\s*</td>\s*'
    r'<td data-cell-title="Game:[^>]*>\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="Date Claimed:[^>]*>\s*([^<]*?)\s*</td>',
    re.IGNORECASE,
)
m2 = list(AR_RELAXED.finditer(r.text))
print("AR relaxed matches: %d" % len(m2))
if m2:
    print("  first:", m2[0].groups())

# Try without the leading <tr>
AR_NO_TR = re.compile(
    r'<td data-cell-title="Name:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="City:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="Amount:\s*">\s*\$?([\d,.]+)\s*</td>\s*'
    r'<td data-cell-title="Game:\s*">\s*([^<]*?)\s*</td>\s*'
    r'<td data-cell-title="Date Claimed:\s*">\s*([^<]*?)\s*</td>',
    re.IGNORECASE,
)
m3 = list(AR_NO_TR.finditer(r.text))
print("AR no-leading-tr matches: %d" % len(m3))

# Show the snippet around first Name td so we can see what's between rows
idx = r.text.find('<td data-cell-title="Name:')
print("\nAR context (around first row):")
print(repr(r.text[max(0, idx-50):idx+200]))

print("\n=== MN ===")
r = requests.get("https://www.mnlottery.com/winners/game?page=1",
                 headers=HEADERS, timeout=30)
print("MN p1: status=%s, len=%d" % (r.status_code, len(r.text)))
MN_CARD_RE = re.compile(
    r'winner-category">\s*([^<]+?)\s*</span>[\s\S]{0,4000}?'
    r'winner-info">\s*([^<]+?)\s*</span>[\s\S]{0,1500}?'
    r'winner-date">\s*([^<]+?)\s*</span>[\s\S]{0,1500}?'
    r'winner-payout">\s*\$?([\d,.]+)\s*</span>',
    re.IGNORECASE,
)
mn_m = list(MN_CARD_RE.finditer(r.text))
print("MN matches: %d" % len(mn_m))
# Show winner-* contexts
print("Has winner-category:", "winner-category" in r.text)
print("Has winner-info:", "winner-info" in r.text)
print("Has winner-date:", "winner-date" in r.text)
print("Has winner-payout:", "winner-payout" in r.text)
# Print first card-ish
for kw in ["winner-category", "winner-payout", "winner-info"]:
    idx = r.text.find(kw)
    print(f"\n{kw} first context:")
    print(repr(r.text[max(0,idx-20):idx+300]))

print("\n=== WI ===")
r = requests.get("https://wilottery.com/winners/all-winners?page=0",
                 headers=HEADERS, timeout=30)
print("WI p0: status=%s, len=%d" % (r.status_code, len(r.text)))
WI_CARD_RE = re.compile(
    r'<div class="vw-big-winners5">[\s\S]*?'
    r'<div class="game-name">\s*([^<]+?)\s*</div>[\s\S]*?'
    r'<div class="prize-amount">\s*\$?([\d,.]+)\s*</div>[\s\S]*?'
    r'<div class="winner-name">\s*([^<]*?)\s*</div>[\s\S]*?'
    r'<div class="date-loc">\s*'
    r'<div>\s*([^<]*?)\s*</div>\s*'
    r'<div>\s*([^<]*?)\s*</div>\s*'
    r'<div>\s*([^<]*?)\s*</div>',
)
wi_m = list(WI_CARD_RE.finditer(r.text))
print("WI matches: %d" % len(wi_m))
print("Has vw-big-winners5:", "vw-big-winners5" in r.text)
print("Has game-name:", 'class="game-name"' in r.text)
print("Has prize-amount:", 'class="prize-amount"' in r.text)
print("Has winner-name:", 'class="winner-name"' in r.text)
print("Has date-loc:", 'class="date-loc"' in r.text)
# show first context
for kw in ['vw-big-winners5', 'game-name', 'prize-amount', 'winner-name', 'date-loc']:
    idx = r.text.find(kw)
    if idx >= 0:
        print(f"\n{kw} first context:")
        print(repr(r.text[max(0, idx-30):idx+500]))
