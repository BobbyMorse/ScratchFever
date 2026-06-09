html=open('wv2.html','r',encoding='utf-8',errors='ignore').read()
labels=['Overall','Odds','overall','Total Tickets','Prize Amount','Prizes Remaining','Remaining','Total Prizes','Game Odds','prizeTable','Prizes']
for kw in labels:
    idx=html.find(kw)
    print(kw,'@',idx, repr(html[idx:idx+60]) if idx>0 else '')
print('---')
# 'Prize' standalone label?
import re
for m in re.finditer(r'>[A-Z][a-zA-Z ]{3,30}<',html):
    s=m.group(0)
    if any(k in s for k in ['Prize','Odds','Total','Remaining','Ticket']):
        print(s)
