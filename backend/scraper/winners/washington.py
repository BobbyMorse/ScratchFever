"""
Washington state winners scraper.

walottery.com publishes its full winners list (~5,600 entries) on a single
~10MB HTML page at /Winners/Search.aspx. No JS, no API, no pagination —
one ASP.NET page with one `<table>` per winner inside
`.search-winners-results-viewport-min`.

Per-winner table shape:

    <table>
      <tr>
        <td><strong>May 29, 2026</strong></td>
        <td>
          <img alt="Scratch" ...>
          <p>$5,000 FRENZY</p>
        </td>
      </tr>
      <tr><td colspan="2"><div class="rule-horizontal"></div></td></tr>
      <tr>
        <td><strong>NAME:</strong> MICHAEL P.</td>
        <td>$1,000</td>
      </tr>
      <tr>
        <td colspan="2">
          <strong>LOCATION:</strong>
          SAFEWAY STORE #1966<br>13101 SE KENT KANGLEY RD, KENT, WA
        </td>
      </tr>
    </table>

The `<img alt="...">` is the game *category* — "Scratch", "Powerball",
"Mega Millions", "Lotto", "Hit 5", "Match 4", "Pick 3", "Cash Pop",
"Daily Keno". We keep only `alt="Scratch"` (big-wins map is scratch-only).

We previously used a single multi-line regex which silently broke after a
template change; BS4 walks the DOM and is robust to whitespace + attribute
shuffles.
"""
from __future__ import annotations
import datetime as dt
import logging
import re

from bs4 import BeautifulSoup

from backend.scraper.winners.base import WinnersScraper

logger = logging.getLogger(__name__)

URL = "https://walottery.com/Winners/Search.aspx"


class WashingtonWinnersScraper(WinnersScraper):
    state_code = "WA"
    state_name = "Washington"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        # 10 MB page typically completes in ~3 s; raise above the base 30 s
        # default to be safe on slow days from a cloud host.
        resp = self.get(URL, timeout=120)
        soup = BeautifulSoup(resp.text, "lxml")

        viewport = soup.select_one(".search-winners-results-viewport-min")
        if not viewport:
            logger.warning("WA: viewport div not found — page layout may have changed")
            return []

        out: list[dict] = []
        for table in viewport.find_all("table"):
            norm = self._parse_table(table)
            if norm:
                out.append(norm)
        logger.info("WA winners: %d scratch wins parsed", len(out))
        return out

    def _parse_table(self, table) -> dict | None:
        img = table.find("img")
        if not img:
            return None
        game_type = (img.get("alt") or "").strip().lower()
        if game_type != "scratch":
            return None  # scratch-only

        # Date — first <strong> in the first row.
        date_el = table.find("strong")
        claim_date = _parse_us_date(date_el.get_text(strip=True) if date_el else "")

        # Game name — <p> next to the img.
        p = table.find("p")
        game_name = p.get_text(" ", strip=True) if p else ""
        if not game_name:
            return None

        name_text = ""
        prize = None
        location_text = ""
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            joined = " ".join(c.get_text(" ", strip=True) for c in cells)
            joined_upper = joined.upper()
            if "NAME:" in joined_upper:
                if len(cells) >= 2:
                    name_text = re.sub(r"^\s*NAME:\s*", "",
                                       cells[0].get_text(" ", strip=True),
                                       flags=re.IGNORECASE).strip()
                    prize = _parse_money(cells[1].get_text(" ", strip=True))
            elif "LOCATION:" in joined_upper:
                cell = cells[0]
                for br in cell.find_all("br"):
                    br.replace_with("\n")
                raw = cell.get_text("\n", strip=True)
                raw = re.sub(r"^\s*LOCATION:\s*", "", raw, flags=re.IGNORECASE).strip()
                location_text = raw

        if prize is None or prize < self.min_prize:
            return None

        retailer_name, retailer_address, retailer_city, retailer_zip = _parse_location(location_text)

        sid_parts = [
            (claim_date.isoformat() if claim_date else ""),
            name_text,
            f"{int(prize)}",
            retailer_name or "",
            game_name,
        ]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": retailer_name,
            "retailer_address": retailer_address,
            "retailer_city": retailer_city,
            "retailer_zip": retailer_zip,
            "winner_city": None,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": URL,
        }


# ── parsing helpers ─────────────────────────────────────────────────────────

_US_DATE_RE = re.compile(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], start=1)}


def _parse_us_date(s: str) -> dt.date | None:
    if not s:
        return None
    m = _US_DATE_RE.search(s)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3].lower())
    if not mon:
        return None
    try:
        return dt.date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None


def _parse_money(s: str) -> float | None:
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", s or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_location(raw: str) -> tuple[str | None, str | None, str | None, str | None]:
    """'SAFEWAY STORE #1966\\n13101 SE KENT KANGLEY RD, KENT, WA' →
    (retailer_name, street, city, zip)."""
    if not raw:
        return None, None, None, None
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    if not lines:
        return None, None, None, None
    retailer_name = lines[0]
    if len(lines) < 2:
        return retailer_name, None, None, None

    addr_line = lines[1]
    parts = [p.strip() for p in addr_line.split(",")]
    street, city, zip_code = None, None, None
    if len(parts) >= 3:
        street = parts[0]
        city = parts[1]
        st_zip = parts[2]
        zm = re.search(r"WA\s*(\d{5})", st_zip)
        if zm:
            zip_code = zm.group(1)
    elif len(parts) == 2:
        street = parts[0]
        city = parts[1]

    if city:
        city = re.sub(r"\s+WA(\s+\d{5})?\s*$", "", city, flags=re.IGNORECASE).strip()
    # Title-case for consistency with other scrapers' winner_city values.
    if city:
        city = city.title()

    return retailer_name, street, city, zip_code
