"""
Fetch all DC Lottery retailers from the static where-to-play page and save to dc_retailers.csv.

Source: https://dclottery.com/player-resources/where-to-play
        The page serves all 326 retailers as static HTML (no JS required).

Fields: name, address, zip_code, phone
No lat/lng or unique IDs are available in the source HTML.

Usage:
    python fetch_dc_retailers.py
"""

import csv
import re
import logging
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH = Path(__file__).parent / "dc_retailers.csv"
URL = "https://dclottery.com/player-resources/where-to-play"
BASE_URL = "https://dclottery.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

FIELDNAMES = ["name", "address", "zip_code", "phone", "state"]

_ZIP_RE   = re.compile(r"^\d{5}(?:-\d{4})?$")
_PHONE_RE = re.compile(r"\(\d{3}\)\s*\d{3}-\d{4}")


def _phone_from_tel(href: str) -> str:
    """Strip the tel: scheme and URL-decode (DC's site encodes '(' and ')')."""
    return unquote(href.replace("tel:", "", 1)).strip()


def fetch_page() -> BeautifulSoup:
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def parse_from_table(table) -> list[dict]:
    """Parse a standard <table> with columns: name, address, zip, phone."""
    rows = table.find_all("tr")
    if not rows:
        return []

    # Detect header row and column positions
    header_cells = rows[0].find_all(["th", "td"])
    col = {}
    for i, cell in enumerate(header_cells):
        t = cell.get_text(strip=True).lower()
        if "name" in t or "location" in t or "store" in t:
            col.setdefault("name", i)
        elif "address" in t or "street" in t:
            col.setdefault("address", i)
        elif "zip" in t or "postal" in t:
            col.setdefault("zip", i)
        elif "phone" in t or "tel" in t:
            col.setdefault("phone", i)

    retailers = []
    data_rows = rows[1:] if col else rows
    for row in data_rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        if col:
            name    = cells[col["name"]].get_text(strip=True)    if "name"    in col and len(cells) > col["name"]    else ""
            address = cells[col["address"]].get_text(strip=True) if "address" in col and len(cells) > col["address"] else ""
            zip_    = cells[col["zip"]].get_text(strip=True)     if "zip"     in col and len(cells) > col["zip"]     else ""
            # Phone may be a tel: link
            if "phone" in col and len(cells) > col["phone"]:
                tel = cells[col["phone"]].find("a", href=re.compile(r"^tel:"))
                phone = (_phone_from_tel(tel["href"]) if tel
                         else cells[col["phone"]].get_text(strip=True))
            else:
                phone = ""
        else:
            # No header — assume name, address, zip, phone by position
            name    = cells[0].get_text(strip=True) if len(cells) > 0 else ""
            address = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            zip_    = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            tel     = cells[3].find("a", href=re.compile(r"^tel:")) if len(cells) > 3 else None
            phone   = (_phone_from_tel(tel["href"]) if tel
                       else (cells[3].get_text(strip=True) if len(cells) > 3 else ""))

        if not name:
            continue
        retailers.append({"name": name, "address": address, "zip_code": zip_, "phone": phone, "state": "DC"})

    return retailers


def parse_from_rows(soup: BeautifulSoup) -> list[dict]:
    """
    Fallback: scan all <tr> elements regardless of table parent.
    Each valid retailer row has 4 cells: name, address, zip, phone.
    Phone cell contains a tel: link.
    """
    retailers = []
    seen = set()

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue

        tel_link = row.find("a", href=re.compile(r"^tel:"))
        texts = [c.get_text(strip=True) for c in cells]

        # Identify the zip cell (5-digit zip code)
        zip_ = ""
        for t in texts:
            if _ZIP_RE.match(t):
                zip_ = t
                break

        if not zip_ and not tel_link:
            continue

        name    = texts[0]
        address = texts[1] if len(texts) > 1 else ""
        phone   = tel_link["href"].replace("tel:", "").strip() if tel_link else (texts[3] if len(texts) > 3 else "")

        key = (name, address)
        if not name or key in seen:
            continue
        seen.add(key)

        retailers.append({"name": name, "address": address, "zip_code": zip_, "phone": phone, "state": "DC"})

    return retailers


def parse_retailers(soup: BeautifulSoup) -> list[dict]:
    # Try the largest table first
    tables = soup.find_all("table")
    if tables:
        best = max(tables, key=lambda t: len(t.find_all("tr")))
        retailers = parse_from_table(best)
        if len(retailers) > 10:
            return retailers

    # Fallback: scan all <tr> rows
    retailers = parse_from_rows(soup)
    if retailers:
        return retailers

    logger.warning("Could not find retailer table; attempting list-element fallback")
    return []


def save_csv(retailers: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(retailers)
    logger.info("Saved %d retailers → %s", len(retailers), path)


def main() -> None:
    logger.info("Fetching %s", URL)
    soup = fetch_page()
    retailers = parse_retailers(soup)

    if not retailers:
        logger.error("No retailers found — inspect the page HTML and update selectors.")
        return

    retailers.sort(key=lambda r: (r["name"].lower(),))
    save_csv(retailers, OUT_PATH)
    logger.info("Done. %d DC retailers saved.", len(retailers))


if __name__ == "__main__":
    main()
