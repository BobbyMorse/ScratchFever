import requests
import re
import json

r = requests.get('https://www.oregonlottery.org/winners/', timeout=15,
                 headers={'User-Agent': 'Mozilla/5.0'})
text = r.text
blocks = re.finditer(
    r'"gametype":"scratch-it","winnerdate":(\d+),"humandate":"([^"]+)","winneramount":"(\d+)","markup":"(.*?)"\},',
    text,
)
hits = []
for b in blocks:
    # Properly unescape JSON-encoded HTML by wrapping with quotes and json.loads
    raw_quoted = '"' + b.group(4) + '"'
    try:
        m_html = json.loads(raw_quoted)
    except Exception:
        continue
    hm = re.search(r'href="([^"]+)"', m_html)
    nm = re.search(r'<small data-winner="">([^<]+)</small>', m_html)
    gm = re.search(
        r'<div class="ol-card__description[^>]*>\s*<p>([^<]+)</p>', m_html, re.DOTALL,
    )
    hd = b.group(2).replace('\\/', '/')
    hits.append((
        hd, b.group(3),
        hm.group(1) if hm else None,
        nm.group(1) if nm else None,
        gm.group(1).strip() if gm else None,
    ))

print('scratch hits:', len(hits))
for h in hits[:10]:
    date, amt, url, nm, gm = h
    print(date, '$' + amt, '|', gm, '|', nm)

# probe a couple of detail pages for city info
for h in hits[:5]:
    date, amt, url, nm, gm = h
    if not url:
        continue
    try:
        rr = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
    except Exception as e:
        print('err', e); continue
    print('====', url)
    for pat in [r'<meta property="og:description" content="([^"]+)"',
                r'<meta name="description" content="([^"]+)"']:
        m = re.search(pat, rr.text)
        if m:
            try:
                print('  ', m.group(1)[:400])
            except UnicodeEncodeError:
                print('  ', m.group(1)[:400].encode('ascii', 'replace').decode())
            break
