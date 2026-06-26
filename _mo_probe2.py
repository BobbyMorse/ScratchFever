import requests, re
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
r = requests.get('https://www.molottery.com/news/monthlywinners.do',
                 params={'method':'Display','y':'2026','m':'5'},
                 headers=HEADERS, timeout=30)
# Print everything that looks like form fields, hidden inputs, selects, headings
import re
for sel in re.findall(r'<select[\s\S]*?</select>', r.text)[:5]:
    print('SELECT:', sel[:600])
    print('---')
for h in re.findall(r'<h[1-4][^>]*>[\s\S]*?</h[1-4]>', r.text)[:20]:
    print('H:', h.strip())
# print labels near the table
# look for the heading just before the first <tr>
idx = r.text.find('<tr>')
print('\n--- 2000 chars before first <tr> ---')
print(r.text[max(0,idx-2000):idx])
print('\n--- first <table> after first <tr> ---')
tbl_idx = r.text.rfind('<table', 0, idx)
print(r.text[tbl_idx:idx+200])
