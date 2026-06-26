import requests, re, sys
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
ROW_RE = re.compile(
    r'<tr>\s*<td>\s*<b>([^<]*)</b>\s*</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>\$([\d,.]+)</td>',
    re.IGNORECASE,
)
for y, m in [(2026, 5), (2026, 4), (2026, 3), (2026, 2), (2025, 12)]:
    r = requests.get('https://www.molottery.com/news/monthlywinners.do',
                     params={'method':'Display','y':str(y),'m':str(m)},
                     headers=HEADERS, timeout=30)
    rows = list(ROW_RE.finditer(r.text))
    monopoly = [mm for mm in rows if 'MONOPOLY' in mm.group(4).upper()]
    print(f'=== {y}-{m:02d}: HTTP {r.status_code}, url={r.url}, total rows={len(rows)}, monopoly rows={len(monopoly)} ===')
    for mm in monopoly[:5]:
        print(' ', mm.groups())
    # Also look for what month the page actually thinks it's showing
    title_m = re.search(r'<title>([^<]+)</title>', r.text)
    h1_m = re.search(r'<h1[^>]*>([^<]+)</h1>', r.text)
    print(f'  title: {title_m.group(1) if title_m else None}')
    print(f'  h1: {h1_m.group(1) if h1_m else None}')
    # length sniff
    print(f'  body length: {len(r.text)}')
