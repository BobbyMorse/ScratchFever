import re

line = '$50,000\t335,870\t4 of 24'
print(repr(line))

m = re.match(
    r"\$([\d,]+(?:\.\d+)?(?:\s*(?:Million|Thousand))?)"
    r"\s+([\d,]+(?:\.\d+)?)"
    r"\s+(\d[\d,]*)\s+of\s+(\d[\d,]*)",
    line, re.IGNORECASE
)
print('match:', m)
if m:
    print('groups:', m.groups())

# Also test with the actual scraper
import sys
sys.path.insert(0, '.')
from backend.scraper.states.arizona import ArizonaScraper
scraper = ArizonaScraper()
games = scraper.scrape()
print(f'\nTotal games found: {len(games)}')
for g in games:
    print(f'  {g["name"]} ${g["price"]} ev={g["ev"]}')
