"""
DC Lottery retailer scraper.
Source: https://dclottery.com/player-resources/where-to-play
Static HTML table — no JS required. ~326 retailers.
"""
from __future__ import annotations
import logging
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup
from .base import make_external_id, safe_get, upsert_retailers

logger = logging.getLogger(__name__)

URL = "https://dclottery.com/player-resources/where-to-play"

_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


def _clean_phone(raw: str) -> str:
    if not raw:
        return ""
    s = unquote(raw).strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return s


def _parse_from_table(table) -> list[dict]:
    rows = table.find_all("tr")
    if not rows:
        return []

    header_cells = rows[0].find_all(["th", "td"])
    col: dict[str, int] = {}
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
            if "phone" in col and len(cells) > col["phone"]:
                tel = cells[col["phone"]].find("a", href=re.compile(r"^tel:"))
                phone = (tel["href"].replace("tel:", "").strip() if tel
                         else cells[col["phone"]].get_text(strip=True))
            else:
                phone = ""
        else:
            name    = cells[0].get_text(strip=True) if len(cells) > 0 else ""
            address = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            zip_    = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            tel     = cells[3].find("a", href=re.compile(r"^tel:")) if len(cells) > 3 else None
            phone   = (tel["href"].replace("tel:", "").strip() if tel
                       else (cells[3].get_text(strip=True) if len(cells) > 3 else ""))

        if not name:
            continue
        retailers.append({
            "external_id": make_external_id(name, address),
            "name": name,
            "address": address or None,
            "city": "Washington",
            "zip_code": zip_ or None,
            "phone": phone or None,
            "latitude": None,
            "longitude": None,
        })

    return retailers


def _parse_from_rows(soup: BeautifulSoup) -> list[dict]:
    retailers = []
    seen: set[tuple] = set()

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue

        tel_link = row.find("a", href=re.compile(r"^tel:"))
        texts = [c.get_text(strip=True) for c in cells]

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

        retailers.append({
            "external_id": make_external_id(name, address),
            "name": name,
            "address": address or None,
            "city": "Washington",
            "zip_code": zip_ or None,
            "phone": phone or None,
            "latitude": None,
            "longitude": None,
        })

    return retailers


def scrape_dc() -> list[dict]:
    resp = safe_get(URL, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    if resp is None:
        logger.error("DC: failed to fetch %s", URL)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    tables = soup.find_all("table")
    if tables:
        best = max(tables, key=lambda t: len(t.find_all("tr")))
        retailers = _parse_from_table(best)
        if len(retailers) > 10:
            logger.info("DC: scraped %d retailers", len(retailers))
            return retailers

    retailers = _parse_from_rows(soup)
    logger.info("DC: scraped %d retailers (row fallback)", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_dc)
    return await upsert_retailers(conn, "DC", retailers)
