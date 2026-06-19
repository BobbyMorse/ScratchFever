import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}
r = requests.get("https://www.mslottery.com/instantgames/triple-red-7s/", headers=HEADERS, timeout=20)
soup = BeautifulSoup(r.text, "lxml")
text = soup.get_text(" ", strip=True)

h1 = soup.find("h1")
print("H1:", repr(h1.get_text(strip=True)) if h1 else None)

patterns = [
    r"\(\$(\d+)\)",
    r"ticket\s+price[:\s]+\$?([\d.]+)",
    r"price[:\s]+\$?([\d.]+)",
    r"\$(\d+)\s+(?:ticket|game)",
]
for pat in patterns:
    m = re.search(pat, text, re.I)
    print(f"  {pat:55} -> {m.group(0) if m else None}")

idx = text.lower().find("price")
print("---price context:", repr(text[max(0, idx - 30):idx + 200]) if idx > 0 else "NOT FOUND")

idx = text.lower().find("overall")
print("---overall context:", repr(text[max(0, idx - 30):idx + 200]) if idx > 0 else "NOT FOUND")

tables = soup.find_all("table")
print(f"Tables: {len(tables)}")
for i, t in enumerate(tables[:5]):
    hdrs = [th.get_text(strip=True) for th in t.find_all("th")]
    print(f"  table {i} headers: {hdrs}")
    rows = t.find_all("tr")
    if rows and len(rows) > 1:
        first_data = [c.get_text(strip=True) for c in rows[1].find_all(["td", "th"])]
        print(f"      first data row: {first_data}")
