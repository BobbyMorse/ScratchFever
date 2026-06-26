import requests, hashlib, json
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
}
URL = "https://www.palottery.pa.gov/Custom/ebw/JsWinners.aspx"
combos = [
    ("PHILADELPHIA", 2026, "May"),
    ("PHILADELPHIA", 2026, "January"),
    ("PHILADELPHIA", 2024, "July"),
    ("ALLEGHENY",    2026, "May"),
    ("ALLEGHENY",    2024, "July"),
]
for county, year, month in combos:
    r = requests.get(URL, params={"county": county, "year": year, "month": month},
                     headers=HEADERS, timeout=30)
    text = (r.text or "").strip()
    try:
        rows = json.loads(text) if text and text != "[]" else []
    except Exception:
        rows = None
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    print(f"county={county} year={year} month={month}: status={r.status_code} bytes={len(text)} digest={digest} rows={len(rows) if isinstance(rows, list) else 'NOT-JSON'}")
    if isinstance(rows, list) and rows:
        sample = rows[0]
        print("  sample keys:", list(sample.keys()))
        print("  sample year/month from row:", sample.get("year"), sample.get("month"))
