"""
Illinois winners scraper.

illinoislottery.com/winning/instant-game-winners publishes ~1,000 recent
$25K+ instant-game winners on a single page. Cloudflare blocks plain HTTP
(403) but lets Playwright through transparently — same pattern as AZ.
The whole table is server-rendered into the HTML on first load; no
pagination or AJAX needed.

Row shape (one per `<tr>`):

    <td>7641</td>                          # game number (= source_game_id)
    <td>$100,000 CROSSWORD ($5)</td>       # game name + ticket price
    <td>$100,000</td>                       # prize amount
    <td>Westchester Bp,<br>
        11201 Cermak Rd,<br>
        Westchester, Il, 60154</td>         # retailer name + address + city + zip
    <td>04/30/2026</td>                     # claim date

Highest data quality of any state we cover — full retailer street address
means real pins, not city centroids.
"""
from __future__ import annotations
import concurrent.futures
import datetime as dt
import logging
import re

from bs4 import BeautifulSoup

from backend.scraper.winners.base import WinnersScraper

logger = logging.getLogger(__name__)

URL = "https://www.illinoislottery.com/winning/instant-game-winners"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class IllinoisWinnersScraper(WinnersScraper):
    state_code = "IL"
    state_name = "Illinois"
    # IL only publishes $25K+, so the $10K floor is moot — but we keep the
    # base class's filter in place for consistency.
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("IL winners: playwright not installed")
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    def _playwright_scrape(self) -> list[dict]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=UA,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = ctx.new_page()
            try:
                page.goto(URL, wait_until="networkidle", timeout=60_000)
            except Exception as e:
                logger.warning("IL winners goto error: %s", e)
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "lxml")
        out: list[dict] = []
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            # Skip header / layout rows that don't have all 5 fields
            if len(tds) < 5:
                continue
            norm = self._parse_row(tds)
            if norm:
                out.append(norm)
        logger.info("IL winners: %d rows parsed", len(out))
        return out

    def _parse_row(self, tds) -> dict | None:
        game_id_raw = tds[0].get_text(" ", strip=True)
        game_name_raw = tds[1].get_text(" ", strip=True)
        amount_raw = tds[2].get_text(" ", strip=True)

        # Game # must be all digits — skip the header "Game #" row and any
        # other non-data rows.
        if not re.fullmatch(r"\d+", game_id_raw):
            return None

        prize = _parse_money(amount_raw)
        if prize is None or prize < self.min_prize:
            return None

        # IL mixes two column orders in the same table:
        #   newer rows: Game# | Name | $ | Retailer | Date
        #   older rows: Game# | Name | $ | Date     | Retailer
        # Detect which by checking if td[3] is a bare ISO/MDY date string.
        td3_text = tds[3].get_text(" ", strip=True)
        if _ISO_DATE_RE.fullmatch(td3_text) or _MDY_RE.fullmatch(td3_text):
            date_cell, retailer_cell = tds[3], tds[4]
        else:
            retailer_cell, date_cell = tds[3], tds[4]

        # Retailer cell uses <br> or <p> between name / street / city,state,zip.
        # Replace <br>/<p>/</p> boundaries with newline before text extraction.
        for br in retailer_cell.find_all("br"):
            br.replace_with("\n")
        retailer_raw = retailer_cell.get_text("\n", strip=True)
        retailer_name, address, city, zip_code = _parse_retailer(retailer_raw)

        date_raw = date_cell.get_text(" ", strip=True)
        claim_date = _parse_mdy(date_raw) or _parse_iso(date_raw)

        # Strip the trailing "($X)" price marker that IL appends to game names
        # — keep the name cleaner for is_draw_game matching and game-linking.
        game_name = re.sub(r"\s*\(\$\d+\)\s*$", "", game_name_raw).strip()

        # Source id: combine game_id + retailer + date + prize + name. Stable
        # across re-scrapes since IL never re-orders the table.
        sid_parts = [
            game_id_raw,
            (claim_date.isoformat() if claim_date else ""),
            f"{int(prize)}",
            retailer_name or "",
            game_name,
        ]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": game_id_raw,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": retailer_name,
            "retailer_address": address,
            "retailer_city": city,
            "retailer_zip": zip_code,
            "winner_city": None,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": URL,
        }


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_money(s: str) -> float | None:
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", s or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


_MDY_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _parse_mdy(s: str) -> dt.date | None:
    m = _MDY_RE.search(s or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _parse_retailer(raw: str) -> tuple[str | None, str | None, str | None, str | None]:
    """
    Split "Westchester Bp,\\n11201 Cermak Rd,\\nWestchester, Il, 60154" into
    (name, address, city, zip).
    """
    if not raw:
        return None, None, None, None
    lines = [ln.strip().rstrip(",") for ln in raw.split("\n") if ln.strip()]
    if not lines:
        return None, None, None, None
    name = lines[0]
    address = lines[1] if len(lines) > 1 else None
    city = None
    zip_code = None
    if len(lines) > 2:
        # "Westchester, Il, 60154"
        parts = [p.strip() for p in lines[2].split(",")]
        if len(parts) >= 1:
            city = parts[0]
        if len(parts) >= 3 and re.fullmatch(r"\d{5}", parts[2]):
            zip_code = parts[2]
    return name, address, city, zip_code
