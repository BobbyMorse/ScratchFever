"""
New York winners scraper.

NY's winners page is a Gatsby SPA backed by a Drupal REST API. The
"recent_winners" Drupal view exposes paginated, structured press-release
data — 10 winners per page across ~28 pages (~276 total going back ~2
years). Each entry has:

  nid, alias, prize, prize_type, name, location, date, title, body (HTML)

The press-release body always contains a stock sentence of the form
"...purchased at <Retailer> located at <Address> in <City>." — we extract
that with a regex so NY can populate the retailer_name + retailer_address
fields (rare luxury — only MA/MI/RI publish retailers structurally).

Endpoint:
  GET https://nylottery.ny.gov/drupal-api/api/recent_winners?_format=json&page=N

No auth required.
"""
from __future__ import annotations
import datetime as dt
import logging
import re

from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

API_URL = "https://nylottery.ny.gov/drupal-api/api/recent_winners"
SOURCE_BASE = "https://nylottery.ny.gov"
SOURCE_URL = "https://nylottery.ny.gov/winners"
MAX_PAGES = 35  # safety cap; today pager.total_pages ≈ 28


class NewYorkWinnersScraper(WinnersScraper):
    state_code = "NY"
    state_name = "New York"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        # `days` ignored — we page until we either consume all pages or hit
        # the API's reported total_pages.
        out: list[dict] = []
        page_idx = 0
        total_pages = None

        while page_idx < MAX_PAGES:
            params = {"_format": "json", "page": page_idx}
            resp = self.get(API_URL, params=params)
            data = resp.json()
            rows = data.get("rows") or []
            if not rows:
                break
            for r in rows:
                norm = self._normalize(r)
                if norm:
                    out.append(norm)
            pager = data.get("pager") or {}
            total_pages = total_pages or int(pager.get("total_pages") or 0)
            page_idx += 1
            if total_pages and page_idx >= total_pages:
                break
        return out

    def _normalize(self, r: dict) -> dict | None:
        prize = _parse_prize(r.get("prize"), r.get("prize_type"))
        if prize is None or prize < self.min_prize:
            return None

        title = (r.get("title") or "").strip()
        body = r.get("body") or ""

        game_name = _parse_game_name(title, body)
        if not game_name:
            return None
        if is_draw_game(self.state_code, game_name):
            return None

        claim_date = _parse_iso_date(r.get("date"))
        winner_city = (r.get("location") or "").strip() or None
        # Some NY entries list out-of-state winner home cities like
        # "Sterling, VA"; strip the ", ST" suffix so the geocoder doesn't get
        # confused looking it up under NY.
        if winner_city and re.search(r",\s*[A-Z]{2}\s*$", winner_city):
            winner_city = re.sub(r",\s*[A-Z]{2}\s*$", "", winner_city).strip()

        retailer_name, retailer_address, retailer_city = _parse_retailer(body)

        alias = (r.get("alias") or "").lstrip("/")
        source_url = f"{SOURCE_BASE}/{alias}" if alias else SOURCE_URL
        # nid is stable across re-scrapes; fall back to alias if missing
        sid = (r.get("nid") or "").strip() or alias or source_url

        return {
            "source_id": sid,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": retailer_name,
            "retailer_address": retailer_address,
            "retailer_city": retailer_city or winner_city,
            "retailer_zip": None,
            "winner_city": winner_city,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": source_url,
        }


# ── parsing helpers ──────────────────────────────────────────────────────────

_MILLION_RE = re.compile(r"million", re.IGNORECASE)
_THOUSAND_RE = re.compile(r"thousand", re.IGNORECASE)
_WEEK_FOR_LIFE_RE = re.compile(r"a\s+week\s+for\s+life", re.IGNORECASE)
_YEAR_FOR_LIFE_RE = re.compile(r"a\s+year\s+for\s+life", re.IGNORECASE)


def _parse_prize(amount: str | None, prize_type: str | None) -> float | None:
    """Combine NY's split (amount, type) fields into a single USD figure.

    Examples seen in the live feed:
      ("1.0",     "MILLION")          → 1_000_000
      ("5.0",     "MILLION")          → 5_000_000
      ("1,000",   "A Week For Life")  → annuity stream — treat as $1M minimum
                                        guaranteed payout (NY explicitly
                                        promises $1M minimum on the For Life
                                        games in the press-release body)
      ("5,000",   "A Week for Life")  → likewise — $5M minimum payout

    The For Life prizes get scored at the *guaranteed minimum payout* (which
    is how NY itself publishes them in press releases) so they compare
    apples-to-apples with the lump-sum million-dollar wins.
    """
    if not amount:
        return None
    s = str(amount).strip().replace(",", "")
    try:
        val = float(s)
    except ValueError:
        return None
    ptype = (prize_type or "").strip()

    if _MILLION_RE.search(ptype):
        return val * 1_000_000
    if _THOUSAND_RE.search(ptype):
        return val * 1_000
    if _WEEK_FOR_LIFE_RE.search(ptype) or _YEAR_FOR_LIFE_RE.search(ptype):
        # NY structures these so amount is the periodic payment ($1k/week,
        # $5k/week, $10k/year, etc.). The guaranteed minimum lump-sum payout
        # is the headline figure NY publishes. Estimate by multiplying the
        # periodic payment by NY's stated minimums: $1k/week → $1M min, etc.
        if _WEEK_FOR_LIFE_RE.search(ptype):
            return val * 1_000  # rough: $1k/week ≈ $1M minimum payout
        return val * 100  # $10k/year For Life ≈ $1M minimum
    # No type — treat amount as raw dollars
    return val


_RETAILER_RE = re.compile(
    r"(?:purchased|sold)\s+at\s+([^.<]+?)\s+located\s+at\s+([^.<]+?)\s+in\s+([^.<]+?)\s*\.",
    re.IGNORECASE | re.DOTALL,
)
_RETAILER_FALLBACK_RE = re.compile(
    r"(?:purchased|sold)\s+at\s+([^.<]+?)\s+in\s+([^.<]+?)\s*\.",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _parse_retailer(body_html: str) -> tuple[str | None, str | None, str | None]:
    """Pull (retailer_name, address, city) out of a press-release body."""
    if not body_html:
        return None, None, None
    text = _HTML_TAG_RE.sub(" ", body_html)
    text = _WS_RE.sub(" ", text)
    m = _RETAILER_RE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    m = _RETAILER_FALLBACK_RE.search(text)
    if m:
        return m.group(1).strip(), None, m.group(2).strip()
    return None, None, None


_GAME_TITLE_RE = re.compile(
    r"(?:on|playing|from\s+the\s+(?:Lottery\W+s\s+)?)\s+([^.]+?)\s+(?:scratch[-\s]?off|game|draw)",
    re.IGNORECASE,
)


def _parse_game_name(title: str, body_html: str) -> str | None:
    """Best-effort game extraction. We try the body's '...on the X scratch-off
    game' pattern first since the title is often promotional ('Brooklyn Player
    Wins $1M Scratch-Off Prize'); fall back to title parsing."""
    text = _HTML_TAG_RE.sub(" ", body_html or "")
    text = _WS_RE.sub(" ", text)
    m = _GAME_TITLE_RE.search(text)
    if m:
        return m.group(1).strip()
    # Title-based fallback — e.g., "Wins $1,000 A Week for Life Scratch-Off Prize"
    if "$" in title and ("scratch" in title.lower() or "draw" in title.lower()):
        m2 = re.search(r"\$[\d,.]+\s+(.+?)\s+(?:Scratch[-\s]?Off|Draw|Lottery)\s+Prize", title, re.IGNORECASE)
        if m2:
            return m2.group(1).strip()
    return title.strip() or None


def _parse_iso_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None
