"""
Iowa winners scraper.

ialottery.com publishes recent winner press releases at
/Pages/Pressroom/Winners_Main_Dynamic.aspx as an HTML table. Each row links
to a per-winner press release with full retailer + city detail.

Row shape on the listing page:

    <span class="date">5/26/2026</span>
    <a href="../Pressroom/PressReleaseDetail.aspx?winnerID=2162">
       Central Iowa Woman Wins $15,625 Lottery Prize </a>
    <img class="gameLogo-styleImg" alt="'$50,000 Super Crossword' Scratch Game">

For each entry we have date + prize + game name + a stable winnerID, but
the retailer name + winner city live inside the linked press-release page.
The listing alone gives us:

    source_id      = winnerID
    claim_date     = date
    prize_amount   = $ in title
    game_name      = alt-text on the logo image (or fallback from title)
    winner_city    = first capitalized phrase in title before "Man|Woman|..."
                     (e.g. "Central Iowa", "Eastern Iowa", "Jones County",
                     "Clear Lake")

That's enough to populate the Big Wins map without fetching every press
release. Region-style labels like "Central Iowa" won't geocode cleanly,
so we skip the city when it's a region — those entries still land on the
map at the state centroid via the upsert fallback.
"""
from __future__ import annotations
import datetime as dt
import logging
import re

from bs4 import BeautifulSoup

from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.ialottery.com/Pages/Pressroom/Winners_Main_Dynamic.aspx"

# Region phrases that aren't real cities — pgeocode would mis-resolve them.
_REGION_PHRASES = {
    "central iowa", "eastern iowa", "western iowa", "northern iowa",
    "southern iowa", "northeast iowa", "northwest iowa", "southeast iowa",
    "southwest iowa", "north central iowa", "south central iowa",
}


class IowaWinnersScraper(WinnersScraper):
    state_code = "IA"
    state_name = "Iowa"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        resp = self.get(URL, timeout=60)
        soup = BeautifulSoup(resp.text, "lxml")
        out: list[dict] = []
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"winnerID=(\d+)", href)
            if not m:
                continue
            winner_id = m.group(1)
            if winner_id in seen:
                continue
            title = a.get_text(" ", strip=True)
            if "Wins $" not in title and "wins $" not in title.lower():
                continue
            norm = self._parse(winner_id, title, a)
            if not norm:
                continue
            seen.add(winner_id)
            out.append(norm)

        logger.info("IA winners: %d entries parsed", len(out))
        return out

    def _parse(self, winner_id: str, title: str, anchor) -> dict | None:
        prize = _parse_prize(title)
        if prize is None or prize < self.min_prize:
            return None

        # Walk up the row to find the date span and the logo's alt text.
        tr = anchor.find_parent("tr")
        claim_date = None
        game_name = None
        if tr is not None:
            date_span = tr.find("span", class_="date")
            if date_span:
                claim_date = _parse_mdy(date_span.get_text(strip=True))
            logo = tr.find("img")
            if logo and logo.get("alt"):
                game_name = _extract_game_from_alt(logo["alt"])

        if not game_name:
            game_name = _extract_game_from_title(title)
        if not game_name:
            game_name = "Scratch-Off"

        if is_draw_game(self.state_code, game_name):
            return None

        winner_city = _extract_city_from_title(title)
        # Strip region phrases (pgeocode can't centroid those)
        if winner_city and winner_city.lower() in _REGION_PHRASES:
            winner_city = None

        return {
            "source_id": winner_id,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": None,
            "retailer_address": None,
            "retailer_city": None,
            "retailer_zip": None,
            "winner_city": winner_city,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": f"https://www.ialottery.com/Pages/Pressroom/PressReleaseDetail.aspx?winnerID={winner_id}",
        }


# ── parsing helpers ──────────────────────────────────────────────────────────

_MDY_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_PRIZE_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)\s*(million|thousand)?", re.IGNORECASE)
# "Central Iowa Woman Wins $15,625" → captures "Central Iowa"
_TITLE_CITY_RE = re.compile(
    r"^([A-Z][A-Za-z\.\-\s']+?)\s+(?:Man|Woman|Couple|Family|Player|Resident|Pair|Trio|Friends|Group)\s+Wins",
    re.IGNORECASE,
)


def _parse_prize(s: str) -> float | None:
    m = _PRIZE_RE.search(s or "")
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    if suffix == "million":
        val *= 1_000_000
    elif suffix == "thousand":
        val *= 1_000
    return val


def _parse_mdy(s: str) -> dt.date | None:
    m = _MDY_RE.search(s or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _extract_game_from_alt(alt: str) -> str | None:
    """'$50,000 Super Crossword' Scratch Game' → '$50,000 Super Crossword'.

    Strip the surrounding quotes and trailing 'Scratch Game' suffix the IA
    template appends to every game logo.
    """
    if not alt:
        return None
    s = alt.strip()
    s = re.sub(r"\s*Scratch\s+Game\s*$", "", s, flags=re.IGNORECASE).strip()
    s = s.strip("'\"‘’“”")
    return s or None


def _extract_game_from_title(title: str) -> str | None:
    """Fallback: 'Wins $X playing GAME' → GAME."""
    m = re.search(r"playing\s+(.+?)(?:\s+from|\s*\.|$)", title, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_city_from_title(title: str) -> str | None:
    m = _TITLE_CITY_RE.match(title.strip())
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()
