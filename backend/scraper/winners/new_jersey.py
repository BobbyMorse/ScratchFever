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
# Each token must start with a capital — kept case-sensitive even inside
# IGNORECASE patterns via the (?-i:) inline modifier, since we use this
# pattern as a stopping rule and case is what distinguishes "in" (skip)
# from "In" (start of next sentence, still skip) from "Falls" (city).
_PLACE_RAW = r"[A-Z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*){0,3}"
_PLACE = r"(?-i:" + _PLACE_RAW + r")"

# Retailer: starts with capital, no commas (commas separate retailer from
# address). Periods allowed for "St. Mary's", "Wawa #915" style names.
_RETAILER = r"(?-i:[A-Z])[^,<\n]{1,80}?"

# Address: must start with a digit (street numbers do). Token-based so we
# can cap at ~4 tokens after the leading number — prevents the regex from
# greedily swallowing the next sentence when the prose reads
# "...at Foo, 1740 Route 38. The game that started in March has...".
# Allows periods and `#` for "Ave.", "St.", "W.", "Wawa #915" etc.
_ADDRESS = r"\d+(?:\s+[\w#&'-]+\.?){1,4}"

# Lead phrases that precede a retailer callout.
_LEAD = r"(?:at|sold\s+at|drawn\s+at|purchased\s+at|played\s+at|recorded\s+at|ticket\s+(?:was\s+)?(?:sold|drawn|played|purchased)\s+at)"

# Anchor patterns scan the prose for retailer location callouts. Order
# matters — most specific first; later passes use a "no overlap" rule.
ANCHOR_FULL = re.compile(
    _LEAD + r"\s+(?:the\s+)?(" + _RETAILER + r"),\s+"
    r"(" + _ADDRESS + r")\.?,?\s+in\s+"
    r"(" + _PLACE + r")"
    r"(?:,?\s+in\s+(" + _PLACE + r")\s+County)?"
    r"(?:,\s*(" + _PLACE + r")\s+County)?",
    re.IGNORECASE,
)
# "at Big O Stop in Bergen County's Lodi"  (county-first NJ phrasing)
ANCHOR_COUNTY_FIRST = re.compile(
    r"at\s+(?:the\s+)?(" + _RETAILER + r")\s+in\s+"
    r"(" + _PLACE + r")\s+County[’']?s\s+"
    r"(" + _PLACE + r")",
)
# "at Krauzer's, 49 W. Main St. in Ramsey"  (no county trailer)
ANCHOR_ADDR_CITY = re.compile(
    _LEAD + r"\s+(?:the\s+)?(" + _RETAILER + r"),\s+"
    r"(" + _ADDRESS + r")\.?,?\s+in\s+"
    r"(" + _PLACE + r")",
    re.IGNORECASE,
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
        # County is whichever of the two optional trailing groups matched.
        county = _clean(m.group(4)) or _clean(m.group(5))
        anchors.append({
            "retailer": _clean(m.group(1)),
            "address": _clean(m.group(2)),
            "city":    _clean(m.group(3)),
            "county":  county,
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


# Game-name extractors (case-sensitive so we anchor on Title Case proper
# nouns). Tried in order on the 500-char window preceding the anchor.
# Examples that must parse cleanly:
#   "$1,000,000 Ultimate Spectacular is that..."  → Ultimate Spectacular
#   "top prize for the 200X Cash Blitz in Bergen" → 200X Cash Blitz
#   "in the Win for Life! Scratch-Off Game"       → Win for Life!
#   "$100,000 Bingo Extra, $100,000."             → Bingo Extra
#   "the $250,000 Crossword. That ticket..."      → $250,000 Crossword
#   "Jan. 13: Jersey Giant Winnings, $200,000."   → Jersey Giant Winnings
_GAME_PATTERNS = [
    # WeeklyWins format: "Jan. 13: <Game Name>, $Amount."
    re.compile(r"[A-Z][a-z]{2}\.?\s+\d{1,2}:\s+([^,\n]{2,60}?),\s*\$"),
    # "for the <GAME> in <Location>" — prose lede form. Tried before the
    # generic "Scratch-Off" pattern because NJ's headline often reads
    # "$X Million in Cash Blitz Scratch-Off" (where "Million in" is prize
    # framing, not part of the game name) — we want the prose to win.
    re.compile(
        r"for\s+(?:the\s+)?(\$?[\d,]*\s*[A-Z][\w!&'\- ]{2,60}?)\s+in\s+[A-Z]",
    ),
    # "prize in the <GAME>" — alternative lede form
    re.compile(
        r"prize\s+in\s+the\s+(\$?[\d,]*\s*[A-Z][\w!&'\- ]{2,60}?)(?=[.,])",
    ),
    # Inline: "$X,XXX,XXX <GAME Name> is/was/has/game"  ("$1,000,000 Ultimate Spectacular is...")
    re.compile(
        r"\$[\d,]+\s+([A-Z][\w!&'\-]+(?:\s+[A-Z][\w!&'\-]+){1,5})\s+(?:is|was|has|game)\b",
    ),
    # Fallback: "<prize-prefix> <GAME> Scratch-Off" — catches anything the
    # prose forms missed, but last because headline framing pollutes it.
    re.compile(
        r"(?:\$[\d,]+\s+|the\s+)([A-Z][\w!&'\- ]{2,60}?)\s+Scratch[-\s]?[Oo]ff",
    ),
]


def _game_near(body: str, anchor_start: int) -> str | None:
    """Find the game name associated with the anchor by scanning the 500
    chars preceding it. Returns first cleanly-parsed match."""
    window = body[max(0, anchor_start - 500):anchor_start]
    for pat in _GAME_PATTERNS:
        matches = list(pat.finditer(window))
        if not matches:
            continue
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
    name = re.sub(
        r"^(?:top\s+prize\s+(?:for|in)\s+the?\s+|the\s+|a\s+)",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    # Drop trailing "Scratch-Off Game" / "Scratch-Off" suffix when the prose
    # form captured it: "Win for Life! Scratch-Off Game" → "Win for Life!".
    # Don't strip the trailing "!" — some game names legitimately end with
    # one ("Win for Life!", "Cash Pop!").
    name = re.sub(
        r"\s*Scratch[-\s]?[Oo]ff(?:\s+Game)?\s*$",
        "",
        name,
    ).strip()
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
