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
        fetched = 0
        for url, slug_date in candidates[:MAX_FETCH]:
            try:
                norms = self._fetch_and_parse(url, slug_date)
            except Exception as e:
                logger.warning("NJ winners: failed to parse %s: %s", url, e)
                continue
            fetched += 1
            for norm in norms:
                if norm["source_id"] in seen:
                    continue
                seen.add(norm["source_id"])
                out.append(norm)
        logger.info("NJ winners: %d scratch wins from %d/%d press releases",
                    len(out), fetched, len(candidates))
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

    def _fetch_and_parse(self, url: str, slug_date: dt.date) -> list[dict]:
        """Parse a press release into 0..N win records.

        WeeklyWins releases list multiple wins per page; RetailWin releases
        usually have one headline win. We scan the prose for every "at
        RETAILER ... in CITY" anchor, then look in the surrounding text for
        the matching game name + prize.
        """
        resp = self.get(url, timeout=30)
        soup = BeautifulSoup(resp.text, "lxml")

        body_el = soup.find("article") or soup.find("div", class_="news-content") or soup
        body_text = re.sub(r"\s+", " ", body_el.get_text(" ", strip=True))

        if "Scratch-Off" not in body_text and "Scratch Off" not in body_text and "Scratch-off" not in body_text:
            return []

        release_date = _parse_release_date(body_text) or slug_date
        source_url = url if url.startswith("http") else f"{SOURCE_BASE}{url}"

        wins: list[dict] = []
        anchors = _find_retailer_anchors(body_text)
        for anchor in anchors:
            prize = _prize_near(body_text, anchor["match_start"])
            game = _game_near(body_text, anchor["match_start"])
            if not game:
                continue
            if is_draw_game(self.state_code, game):
                continue
            if prize is None or prize < self.min_prize:
                continue

            sid_parts = [
                release_date.isoformat(),
                f"{int(prize)}",
                game,
                anchor["retailer"] or "",
                anchor["city"] or "",
            ]
            wins.append({
                "source_id": "|".join(sid_parts),
                "source_game_id": None,
                "source_game_name": game,
                "prize_amount": prize,
                "claim_date": release_date,
                "retailer_name": anchor["retailer"],
                "retailer_address": anchor["address"],
                "retailer_city": anchor["city"],
                "retailer_zip": None,
                "winner_city": anchor["city"],
                "retailer_lat": None,
                "retailer_lng": None,
                "source_url": source_url,
            })
        return wins


# ── parsing helpers ──────────────────────────────────────────────────────────

# A multi-word capitalized place name: "Jersey City", "Perth Amboy",
# "Little Falls", "Mullica Hill", or single-word "Lodi"/"Ramsey".
# Allows lowercase connectors like "of"/"the" only mid-name.
_PLACE = r"[A-Z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*){0,3}"

# Anchor patterns scan the prose for retailer location callouts. Order
# matters — most specific first; later passes use a "no overlap" rule.
ANCHOR_FULL = re.compile(
    r"(?:at|sold\s+at|drawn\s+at|purchased\s+at|played\s+at|recorded\s+at|ticket\s+was\s+sold\s+at)\s+"
    r"(?:the\s+)?([A-Z][^,.<\n]{1,80}?),\s+"
    r"([^,.<\n]{2,80}?),?\s+in\s+"
    r"(" + _PLACE + r")"
    r"(?:,?\s+in\s+(" + _PLACE + r")\s+County)?"
    r"(?:,\s*(" + _PLACE + r")\s+County)?",
    re.IGNORECASE,
)
# "at Big O Stop in Bergen County's Lodi"  (county-first NJ phrasing)
ANCHOR_COUNTY_FIRST = re.compile(
    r"at\s+(?:the\s+)?([A-Z][^,.<\n]{1,80}?)\s+in\s+"
    r"(" + _PLACE + r")\s+County[’']?s\s+"
    r"(" + _PLACE + r")",
)
# "at Krauzer's, 49 W. Main St. in Ramsey"  (address + city, no county)
ANCHOR_ADDR_CITY = re.compile(
    r"(?:at|sold\s+at|drawn\s+at|purchased\s+at|played\s+at|recorded\s+at)\s+"
    r"(?:the\s+)?([A-Z][^,.<\n]{1,80}?),\s+"
    r"([^,.<\n]{2,80}?)\s+in\s+"
    r"(" + _PLACE + r")",
)


def _find_retailer_anchors(body: str) -> list[dict]:
    """Locate every (retailer, [address], city, [county]) anchor in the prose.

    Spans are stored so the game/prize extractors can scan a window around
    each anchor.
    """
    anchors: list[dict] = []
    used_spans: list[tuple[int, int]] = []

    def _overlaps(start: int, end: int) -> bool:
        return any(not (end <= s or start >= e) for s, e in used_spans)

    # Pass 1: full pattern with county
    for m in ANCHOR_FULL.finditer(body):
        if _overlaps(m.start(), m.end()):
            continue
        used_spans.append((m.start(), m.end()))
        anchors.append({
            "retailer": _clean(m.group(1)),
            "address": _clean(m.group(2)),
            "city":    _clean(m.group(3)),
            "county":  _clean(m.group(4)) if m.lastindex and m.lastindex >= 4 else None,
            "match_start": m.start(),
            "match_end": m.end(),
        })
    # Pass 2: county-first NJ phrasing
    for m in ANCHOR_COUNTY_FIRST.finditer(body):
        if _overlaps(m.start(), m.end()):
            continue
        used_spans.append((m.start(), m.end()))
        anchors.append({
            "retailer": _clean(m.group(1)),
            "address":  None,
            "city":     _clean(m.group(3)),
            "county":   _clean(m.group(2)),
            "match_start": m.start(),
            "match_end": m.end(),
        })
    # Pass 3: address + city, no county
    for m in ANCHOR_ADDR_CITY.finditer(body):
        if _overlaps(m.start(), m.end()):
            continue
        used_spans.append((m.start(), m.end()))
        anchors.append({
            "retailer": _clean(m.group(1)),
            "address":  _clean(m.group(2)),
            "city":     _clean(m.group(3)),
            "county":   None,
            "match_start": m.start(),
            "match_end": m.end(),
        })
    anchors.sort(key=lambda a: a["match_start"])
    return anchors


_PRIZE_TOKEN_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|m\b|thousand|k\b)?",
    re.IGNORECASE,
)


def _prize_near(body: str, anchor_start: int) -> float | None:
    """Look in the ~500 chars preceding the retailer anchor for the largest
    plausible prize amount. Skips ticket prices and addresses."""
    window_start = max(0, anchor_start - 500)
    window = body[window_start:anchor_start]
    best = 0.0
    for m in _PRIZE_TOKEN_RE.finditer(window):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        suffix = (m.group(2) or "").lower()
        if "million" in suffix or suffix == "m":
            val *= 1_000_000
        elif "thousand" in suffix or suffix == "k":
            val *= 1_000
        # Skip "$X ticket", "$X game", "$X bet" — these are ticket prices.
        tail = window[m.end():m.end() + 14].lower()
        if any(t in tail for t in (" ticket", " game", " bet", " play")):
            continue
        if val > best:
            best = val
    return best if best > 0 else None


# "Million in Cash Blitz", "200X Cash Blitz", "Crossword Bonanza",
# "Ultimate Spectacular", "Win for Life!", "$100,000 Bingo Extra",
# "Jersey Giant Winnings", "Millionaire Maker", "$250,000 Crossword"
_GAME_RE = re.compile(
    r"(?:the\s+|in\s+the\s+|playing\s+|for\s+(?:the\s+)?|of\s+(?:the\s+)?|in\s+)"
    r"(\$?[\d,]*\s*[A-Z][A-Za-z0-9 '!\-\$&]{2,60}?)"
    r"\s*(?:Scratch[-\s]?Off|Scratch[-\s]?off)",
    re.IGNORECASE,
)
# Headline-style with prize prefix: "$200,000. The top prize of the $5 game..."
_DATE_PREFIX_GAME_RE = re.compile(
    r"[A-Z][a-z]{2}\.?\s+\d{1,2}:\s+(.+?),\s*\$",
)


def _game_near(body: str, anchor_start: int) -> str | None:
    """Find the game name associated with the anchor by scanning the 500
    chars preceding it. WeeklyWins format also uses "Mmm DD: Game, $Amount."
    which we check as a fast path."""
    window_start = max(0, anchor_start - 500)
    window = body[window_start:anchor_start]

    # Fast path: "Jan. 13: Game Name, $200,000."
    matches = list(_DATE_PREFIX_GAME_RE.finditer(window))
    if matches:
        name = _clean_game(matches[-1].group(1))
        if name:
            return name

    # General path: nearest "GAME Scratch-Off" before the anchor
    matches = list(_GAME_RE.finditer(window))
    if matches:
        name = _clean_game(matches[-1].group(1))
        if name:
            return name
    return None


def _clean_game(raw: str) -> str | None:
    if not raw:
        return None
    name = re.sub(r"\s+", " ", raw).strip().strip(".,!")
    # Drop leading prize prefix: "$100,000 Bingo Extra" → "Bingo Extra"
    name = re.sub(r"^\$[\d,]+\s+", "", name).strip()
    # Drop common prose lead-ins that creep in
    name = re.sub(r"^(?:top\s+prize\s+for\s+the?\s+|the\s+)", "", name, flags=re.IGNORECASE).strip()
    if not name or len(name) > 80:
        return None
    return name


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
