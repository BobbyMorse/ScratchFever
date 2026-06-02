"""
New Jersey winners scraper.

NJ Lottery's news/press-release feed is the only structured source of
big-prize winner data. The site has no JSON API and the weekly summary
pages are PNG images (useless for scraping), but individual press releases
ARE structured prose with consistent patterns.

Discovery: sitemap.xml lists ~3,200 press releases. Filename prefixes
identify game type:
  SO_   — Scratch-Off (what we want)
  IG_   — Instant Games (legacy scratch)
  PB_   — Powerball         (draw, skip)
  MM_   — Mega Millions     (draw, skip)
  JC5_  — Jersey Cash 5     (draw, skip)
  P6_   — Pick-6            (draw, skip)
  C4L_  — Cash4Life         (draw, skip)
  FP_   — Fast Play         (draw, skip)
  NJL_  — Weekly summaries  (PNG images, useless)
  ...

We pull the sitemap, filter to SO/IG/INST press releases dated within the
lookback window (MMDDYY in filename), then fetch each and parse retailer
+ city + prize via regex on the prose body.

Press release prose typically reads:
  "...purchased a $30 ticket at the Big Pantry Food Market, 858 Amboy Ave.,
   in Perth Amboy in Middlesex County."

Headline carries the game name and prize, e.g.:
  "Middlesex County Player Wins $1 Million Playing Ultimate Spectacular"

This is a low-volume feed (~10-15 scratch wins/year published as press
releases) but each win has retailer + address + county, so geocoding hits
~95%. Use a generous lookback (180+ days) since releases trickle in.
"""
from __future__ import annotations
import datetime as dt
import logging
import re

from bs4 import BeautifulSoup

from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://www.njlottery.com/sitemap.xml"
SOURCE_BASE = "https://www.njlottery.com"

# Filename prefixes we treat as scratch-off press releases. Everything else
# is a draw game or non-winner content. Order doesn't matter — checked as set.
SCRATCH_PREFIXES = ("SO", "IG", "INST")

# Cap how many press releases we fetch per scrape run so we don't hammer
# their CDN if the lookback window opens wide. Sized to comfortably cover
# ~2 years of scratch releases at current publication rate.
MAX_FETCH = 60

# Minimum lookback. NJ publishes scratch press releases sporadically, often
# weeks between entries — a 14-day window would miss most of them, so we
# floor at 180 days to keep hourly scrapes useful while accumulating history.
MIN_LOOKBACK_DAYS = 180

URL_DATE_RE = re.compile(r"_(\d{2})(\d{2})(\d{2})\.html$")
SITEMAP_LOC_RE = re.compile(r"<loc>([^<]+/press-releases/[^<]+)</loc>")

# Retailer-prose patterns, tried in order. Real-world NJ releases vary in
# punctuation; these cover the common shapes observed across 2018-2026.
RETAILER_PATTERNS = (
    # "at the Big Pantry Food Market, 858 Amboy Ave., in Perth Amboy in Middlesex County"
    re.compile(
        r"at\s+(?:the\s+)?([^,.<\n]+?),\s+"
        r"([^,.<\n]+?),?\s+in\s+"
        r"([^,.<\n]+?)\s+in\s+"
        r"([A-Z][a-zA-Z ]+?)\s+County",
        re.IGNORECASE,
    ),
    # "at the Big Pantry Food Market, 858 Amboy Ave., in Perth Amboy"
    re.compile(
        r"at\s+(?:the\s+)?([^,.<\n]+?),\s+"
        r"([^,.<\n]+?),?\s+in\s+"
        r"([A-Z][a-zA-Z .'-]+?)(?:[.,]|$)",
        re.IGNORECASE,
    ),
    # "at Wawa #915 in Medford township"  (no street address)
    re.compile(
        r"at\s+(?:the\s+)?([^,.<\n]+?)\s+in\s+"
        r"([A-Z][a-zA-Z .'-]+?)(?:[.,]|$)",
        re.IGNORECASE,
    ),
)

# Headline prize patterns
HEADLINE_PRIZE_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|thousand)?",
    re.IGNORECASE,
)
# Inline body prize ("won the $1,000,000 top prize", "claimed a $500,000 prize")
BODY_PRIZE_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|m\b)?",
    re.IGNORECASE,
)


class NewJerseyWinnersScraper(WinnersScraper):
    state_code = "NJ"
    state_name = "New Jersey"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        lookback_days = max(days, MIN_LOOKBACK_DAYS)
        cutoff = dt.date.today() - dt.timedelta(days=lookback_days)

        candidates = self._sitemap_urls(cutoff)
        if not candidates:
            logger.info("NJ winners: no candidate press releases within %dd", lookback_days)
            return []

        out: list[dict] = []
        seen: set[str] = set()
        for url, slug_date in candidates[:MAX_FETCH]:
            try:
                norm = self._fetch_and_parse(url, slug_date)
            except Exception as e:
                logger.warning("NJ winners: failed to parse %s: %s", url, e)
                continue
            if not norm:
                continue
            if norm["source_id"] in seen:
                continue
            seen.add(norm["source_id"])
            out.append(norm)
        logger.info("NJ winners: %d scratch wins from %d press releases", len(out), len(candidates))
        return out

    def _sitemap_urls(self, cutoff: dt.date) -> list[tuple[str, dt.date]]:
        """Return [(url, slug_date)] for SO/IG/INST press releases ≥ cutoff,
        most-recent first."""
        try:
            resp = self.get(SITEMAP_URL, timeout=60)
        except Exception as e:
            logger.warning("NJ winners: sitemap fetch failed: %s", e)
            return []

        found: list[tuple[str, dt.date]] = []
        for m in SITEMAP_LOC_RE.finditer(resp.text):
            url = m.group(1)
            # Filter to scratch prefixes only.
            slash_idx = url.rfind("/")
            if slash_idx < 0:
                continue
            filename = url[slash_idx + 1:]
            prefix = filename.split("_", 1)[0]
            if prefix not in SCRATCH_PREFIXES:
                continue
            dm = URL_DATE_RE.search(filename)
            if not dm:
                continue
            try:
                slug_date = dt.date(2000 + int(dm.group(3)), int(dm.group(1)), int(dm.group(2)))
            except ValueError:
                continue
            if slug_date < cutoff:
                continue
            found.append((url, slug_date))
        found.sort(key=lambda t: t[1], reverse=True)
        return found

    def _fetch_and_parse(self, url: str, slug_date: dt.date) -> dict | None:
        resp = self.get(url, timeout=30)
        soup = BeautifulSoup(resp.text, "lxml")

        title_el = soup.find(["h1", "h2"])
        headline = (title_el.get_text(" ", strip=True) if title_el else "").strip()

        body_el = soup.find("article") or soup.find("div", class_="news-content") or soup
        body_text = body_el.get_text(" ", strip=True)
        body_text = re.sub(r"\s+", " ", body_text)

        game_name = _parse_game_name(headline, body_text)
        if not game_name:
            return None
        if is_draw_game(self.state_code, game_name):
            return None

        prize = _parse_prize(headline) or _parse_prize_body(body_text)
        if prize is None or prize < self.min_prize:
            return None

        retailer, address, city, county = _parse_retailer(body_text)

        # Prefer release date from body; fall back to slug date.
        release_date = _parse_release_date(body_text) or slug_date

        sid_parts = [
            release_date.isoformat(),
            f"{int(prize)}",
            game_name,
            retailer or "",
            city or "",
        ]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": release_date,
            "retailer_name": retailer,
            "retailer_address": address,
            "retailer_city": city,
            "retailer_zip": None,
            "winner_city": city,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": url if url.startswith("http") else f"{SOURCE_BASE}{url}",
        }


# ── parsing helpers ──────────────────────────────────────────────────────────

_GAME_HEADLINE_RE = re.compile(
    r"Playing\s+([A-Z0-9][A-Za-z0-9 '\-\$!&]+?)\s*$",
    re.IGNORECASE,
)
_GAME_TICKET_RE = re.compile(
    r"\$\d[\d,]*\s+(?:ticket\s+)?(?:from|of)?\s*(?:the\s+)?([A-Z][A-Za-z0-9 '\-!&]+?)\s+(?:Scratch[-\s]?Off|scratch[-\s]?off|instant)",
    re.IGNORECASE,
)
_GAME_PLAYED_RE = re.compile(
    r"playing\s+(?:the\s+)?([A-Z][A-Za-z0-9 '\-!&]+?)\s+(?:Scratch[-\s]?Off|scratch[-\s]?off|instant)",
    re.IGNORECASE,
)


def _parse_game_name(headline: str, body: str) -> str | None:
    # Order: headline-tail "Playing X" → body "$X ticket of GAME Scratch-Off"
    # → body "playing GAME Scratch-Off" → fall back to None (skip).
    for src, pat in (
        (headline, _GAME_HEADLINE_RE),
        (body, _GAME_TICKET_RE),
        (body, _GAME_PLAYED_RE),
    ):
        m = pat.search(src or "")
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip().strip(".,!")
            if name and len(name) <= 80:
                return name
    return None


def _parse_prize(headline: str) -> float | None:
    if not headline:
        return None
    m = HEADLINE_PRIZE_RE.search(headline)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    if "million" in suffix:
        val *= 1_000_000
    elif "thousand" in suffix:
        val *= 1_000
    elif val < 1000:
        # Bare "$1" / "$5" with no unit is almost always shorthand for millions
        # in headline form ("Wins $1 Million" parsed as "$1" + "Million" by the
        # main path; this is the safety net for parsing oddities).
        val *= 1_000_000
    return val


def _parse_prize_body(body: str) -> float | None:
    if not body:
        return None
    # Prefer the largest $-figure in the first ~600 chars (the lede typically
    # restates the prize). Skip obvious price callouts ("$30 ticket", "$1 game").
    snippet = body[:600]
    best = 0.0
    for m in BODY_PRIZE_RE.finditer(snippet):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        # Skip ticket prices: "$30 ticket", "$1 ticket", "$10 game"
        tail = snippet[m.end():m.end() + 12].lower()
        if any(t in tail for t in (" ticket", " game", " bet")):
            continue
        # "Million" suffix
        if "million" in tail:
            v *= 1_000_000
        if v > best:
            best = v
    return best or None


def _parse_retailer(body: str) -> tuple[str | None, str | None, str | None, str | None]:
    if not body:
        return None, None, None, None
    for pat in RETAILER_PATTERNS:
        m = pat.search(body)
        if not m:
            continue
        groups = m.groups()
        retailer = _clean(groups[0])
        if len(groups) == 4:
            address = _clean(groups[1])
            city = _clean(groups[2])
            county = _clean(groups[3])
        elif len(groups) == 3:
            address = _clean(groups[1])
            city = _clean(groups[2])
            county = None
        else:
            address = None
            city = _clean(groups[1])
            county = None
        return retailer, address, city, county
    return None, None, None, None


_RELEASE_DATE_RE = re.compile(
    r"\(([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\)",
)
_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS.update({f"{m[:3]}.": i for m, i in list(_MONTHS.items()) if not m.endswith(".")})


def _parse_release_date(body: str) -> dt.date | None:
    if not body:
        return None
    m = _RELEASE_DATE_RE.search(body)
    if not m:
        return None
    mon_name = m.group(1)
    mon = _MONTHS.get(mon_name) or _MONTHS.get(mon_name.rstrip("."))
    if not mon:
        return None
    try:
        return dt.date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None


def _clean(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip().strip(".,;")
    return s or None
