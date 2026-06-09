"""
Oregon winners scraper.

oregonlottery.org/winners/ embeds the full winner set as JSON-encoded HTML
inside a JS payload. Each scratch-it entry has:
  "gametype":"scratch-it","winnerdate":<unix>,"humandate":"MM/DD/YY",
  "winneramount":"<int>","markup":"<escaped HTML card>"

The markup includes the detail page URL and a small data-winner tag of the
form "<city or last-init>, MM/DD/YY". When the value before the comma is
a city (Bend, Portland, Springfield, etc.), we use it as the winner_city
for the centroid geocoder; otherwise the row stays uncoded.

No retailer info is published. ~68 scratch wins total in the embedded feed.
"""
from __future__ import annotations
import datetime as dt
import json
import logging
import re

from backend.scraper.winners.base import WinnersScraper

logger = logging.getLogger(__name__)

URL = "https://www.oregonlottery.org/winners/"

ENTRY_RE = re.compile(
    r'"gametype":"scratch-it","winnerdate":(\d+),'
    r'"humandate":"([^"]+)","winneramount":"(\d+)","markup":"(.*?)"\},',
    re.DOTALL,
)
HREF_RE = re.compile(r'href="([^"]+)"')
SMALL_RE = re.compile(r'<small data-winner="">([^<]+)</small>')
GAME_RE = re.compile(
    r'<div class="ol-card__description[^>]*>\s*<p>([^<]+)</p>',
    re.DOTALL,
)
# Listing cards put a marketing headline in <p>, not the official game name.
# Anything that ends with an exclamation (e.g. "Ka Pow!") is plausibly the
# game name; otherwise we just store the headline verbatim.

# A modest set of known OR scratch cities used to validate the <small> tag —
# anything not matching is treated as a non-city (last initial / month tag).
# The geocoder is permissive, but this avoids "F" or "M" being treated as a
# city name.
_OR_CITY_RE = re.compile(r'^[A-Z][A-Za-z\'\- ]{2,}$')


def _decode_markup(raw: str) -> str:
    # Wrap the captured payload as a JSON string so json.loads handles all
    # escapes uniformly (\\n, \\/, \\", \\u2019, ...).
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        return (raw.replace('\\/', '/').replace('\\"', '"')
                  .replace('\\n', '\n').replace('\\t', '\t'))


class OregonWinnersScraper(WinnersScraper):
    state_code = "OR"
    state_name = "Oregon"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        # OR's /winners/ page is a rolling ~68-win snapshot (~3 mo deep),
        # not a date-anchored newsfeed — entries roll off as new ones land.
        # A 14d window almost always returns 0; floor at 180d so the upserter
        # actually sees the published rows.
        lookback_days = max(days, 180)
        cutoff = dt.date.today() - dt.timedelta(days=lookback_days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()

        for m in ENTRY_RE.finditer(resp.text):
            ts = int(m.group(1))
            try:
                claim_date = dt.datetime.utcfromtimestamp(ts).date()
            except (ValueError, OSError, OverflowError):
                continue
            if claim_date < cutoff:
                continue
            try:
                prize = float(m.group(3))
            except ValueError:
                continue
            if prize < self.min_prize:
                continue

            markup = _decode_markup(m.group(4))
            href_m = HREF_RE.search(markup)
            url = href_m.group(1) if href_m else None

            small_m = SMALL_RE.search(markup)
            winner_city = None
            if small_m:
                left = small_m.group(1).split(",", 1)[0].strip()
                if _OR_CITY_RE.match(left) and len(left) >= 3:
                    winner_city = left

            game_m = GAME_RE.search(markup)
            game = (game_m.group(1).strip() if game_m else "") or "Oregon Scratch-it"

            sid_parts = [
                claim_date.isoformat(),
                f"{int(prize)}",
                game,
                winner_city or "",
                url or "",
            ]
            source_id = "|".join(sid_parts)
            if source_id in seen:
                continue
            seen.add(source_id)

            out.append({
                "source_id": source_id,
                "source_game_id": None,
                "source_game_name": game,
                "prize_amount": prize,
                "claim_date": claim_date,
                "retailer_name": None,
                "retailer_city": None,
                "retailer_address": None,
                "retailer_zip": None,
                "winner_city": winner_city,
                "retailer_lat": None,
                "retailer_lng": None,
                "source_url": url or URL,
            })
        return out
