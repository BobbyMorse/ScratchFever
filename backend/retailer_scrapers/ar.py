"""
Arkansas Lottery retailer scraper.
Source: Drupal Views, paginated.
  GET https://www.myarkansaslottery.com/retailer-locator?page=N

10 rows per page; ~90 pages total (~887 retailers). Each row's HTML contains
title, street, city, ZIP, and a "[lat,lng]" geolocation string in a hidden field.
"""
from __future__ import annotations
import logging
import re
from bs4 import BeautifulSoup
from .base import safe_get, make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

URL = "https://www.myarkansaslottery.com/retailer-locator"
MAX_PAGES = 200  # hard cap; loop exits earlier when a page yields 0 rows

_GEO_RE = re.compile(r"\[([-\d.]+)\s*,\s*([-\d.]+)\]")


def _parse_row(div) -> dict | None:
    def _text(cls: str) -> str:
        el = div.find(class_=cls)
        return el.get_text(" ", strip=True) if el else ""

    name = _text("views-field-title")
    if not name:
        return None
    street = _text("views-field-field-address-thoroughfare")
    city = _text("views-field-field-address-locality")
    zip_code = _text("views-field-field-address-postal-code")

    lat = lng = None
    geo = _text("views-field-field-geolocation-address")
    g = _GEO_RE.search(geo)
    if g:
        try:
            lat, lng = float(g.group(1)), float(g.group(2))
        except ValueError:
            pass

    return {
        "external_id": make_external_id(name, street, city, zip_code),
        "name": name,
        "address": street or None,
        "city": city or None,
        "zip_code": zip_code or None,
        "phone": None,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_ar() -> list[dict]:
    retailers: list[dict] = []
    seen: set[str] = set()
    empty_streak = 0

    for page in range(MAX_PAGES):
        resp = safe_get(URL, params={"page": page})
        if resp is None:
            empty_streak += 1
            if empty_streak >= 3:
                break
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        rows = soup.find_all("div", class_="views-row")
        if not rows:
            # No more results
            break

        new_count = 0
        for row in rows:
            r = _parse_row(row)
            if r and r["external_id"] not in seen:
                seen.add(r["external_id"])
                retailers.append(r)
                new_count += 1

        empty_streak = 0
        if (page + 1) % 20 == 0:
            logger.info("AR: page %d done, %d retailers so far", page + 1, len(retailers))

        # Done when fewer than full page of new rows AND we've collected a meaningful total
        if new_count == 0 and len(retailers) > 0:
            break

    logger.info("AR: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_ar)
    return await upsert_retailers(conn, "AR", retailers)
