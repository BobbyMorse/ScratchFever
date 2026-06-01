"""
Nebraska winners scraper.

nelottery.com/homeapp/winners/ renders ~400 recent winner articles
inline on one page. No JS, no API — straight HTML, plain HTTP works.

Each entry is wrapped in an `<a href="/homeapp/article/<id>/display">`
linking to the full press release. The story body always follows the
stock template:

    MM/DD/YYYY - NAME of CITY won $AMOUNT playing GAME, [the|a] [$X]
                  Nebraska Lottery [$X] Scratch game.

Variants we have to handle:
  • Draw games end with " from the Nebraska Lottery."   (filtered out)
  • Lotto games end with ", the Nebraska Lottery $5 Lotto game." (filtered)
  • Million-dollar wins use "$1 million" instead of digits — parse "million"

No retailer info exposed in the listing. Winner home city feeds pgeocode
for the centroid pin.
"""
from __future__ import annotations
import datetime as dt
import logging
import re

from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://nelottery.com/homeapp/winners/"

# Pull the full body block. Stops at any tag, since the body is plain text
# wrapped in <p class="bodytext">.
ARTICLE_RE = re.compile(
    r'/homeapp/article/(\d+)/display[^>]*"'
    r'[\s\S]{0,400}?'
    r'<p class="bodytext">\s*([^<]+?)\s*<',
    re.IGNORECASE,
)

# Body parser. Captures: date, name, city, prize amount text, game name, suffix.
# The suffix tells us scratch vs draw; the game name is between "playing " and
# the suffix's leading separator (", the" or " from the").
BODY_RE = re.compile(
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*"
    r"(?P<name>[A-Z][^,]+?)\s+of\s+"
    r"(?P<city>[A-Z][A-Za-z\.\-\s]+?)\s+won\s+"
    r"\$(?P<prize>[\d,]+(?:\s+million|\s+thousand)?)\s+"
    r"playing\s+(?P<game>.+?)"
    r"(?:,\s+(?:the|a)\s+(?:\$\d+\s+)?Nebraska\s+Lottery\b.+?(?P<scratch>Scratch|Lotto)\s+game"
    r"|\s+from\s+the\s+Nebraska\s+Lottery)\s*\.",
    re.IGNORECASE | re.DOTALL,
)


class NebraskaWinnersScraper(WinnersScraper):
    state_code = "NE"
    state_name = "Nebraska"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        resp = self.get(URL, timeout=60)
        html = resp.text
        out: list[dict] = []
        seen: set[str] = set()
        for m in ARTICLE_RE.finditer(html):
            article_id, body = m.group(1), m.group(2)
            norm = self._parse(article_id, body)
            if not norm:
                continue
            if norm["source_id"] in seen:
                continue
            seen.add(norm["source_id"])
            out.append(norm)
        logger.info("NE winners: %d entries parsed", len(out))
        return out

    def _parse(self, article_id: str, body: str) -> dict | None:
        m = BODY_RE.search(body)
        if not m:
            return None

        scratch_flag = (m.group("scratch") or "").strip().lower()
        # Drop draws/lotto. If no "Scratch game" or "Lotto game" suffix matched,
        # the body ended with " from the Nebraska Lottery." → also a draw.
        if scratch_flag != "scratch":
            return None

        prize = _parse_prize(m.group("prize"))
        if prize is None or prize < self.min_prize:
            return None

        game_name = re.sub(r"\s+", " ", m.group("game")).strip()
        # NE puts the ticket price prefix in the press-release body (e.g.
        # "$100,000 Crossword Craze, the Nebraska Lottery $10 Scratch game")
        # — that's already part of the game name. Leave as-is.
        if is_draw_game(self.state_code, game_name):
            return None

        try:
            mm, dd, yyyy = m.group("date").split("/")
            claim_date = dt.date(int(yyyy), int(mm), int(dd))
        except ValueError:
            claim_date = None

        winner_city = re.sub(r"\s+", " ", m.group("city")).strip()

        return {
            "source_id": article_id,
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
            "source_url": f"https://nelottery.com/homeapp/article/{article_id}/display",
        }


def _parse_prize(raw: str) -> float | None:
    """'$17,777' → 17777, '$1 million' → 1_000_000, '$5 thousand' → 5000."""
    if not raw:
        return None
    s = raw.strip().lower()
    m = re.match(r"([\d,]+(?:\.\d+)?)\s*(million|thousand)?", s)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = m.group(2)
    if suffix == "million":
        val *= 1_000_000
    elif suffix == "thousand":
        val *= 1_000
    return val
