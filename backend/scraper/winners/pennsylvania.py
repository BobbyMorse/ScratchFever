"""
Pennsylvania winners scraper.

PA Lottery publishes a per-county, per-month JSON listing at:
  https://www.palottery.pa.gov/Custom/ebw/JsWinners.aspx?county=X&year=Y&month=Month

Per-win fields: name (winner first + last initial), city (WINNER home city),
county, state, year, month, game_name, is_instant ("true"/"false"),
is_fastplay, prize_amount ("$N,NNN.NN").

There is NO retailer field. We store the winner's home city in `winner_city`
and rely on the city-centroid geocoder at upsert time.
"""
from __future__ import annotations
import calendar
import datetime as dt
import json
import logging
import time
from backend.scraper.winners.base import WinnersScraper, is_draw_game


def _month_end_or_today(year: int, month: int, today: dt.date) -> dt.date:
    """Latest plausible claim date for a month-granularity win. Using the
    first-of-month placeholder caused every prior-month win to age out of the
    30d window before the next month's data was published, leaving the state
    permanently empty."""
    last_day = calendar.monthrange(year, month)[1]
    candidate = dt.date(year, month, last_day)
    return min(candidate, today)

logger = logging.getLogger(__name__)

URL = "https://www.palottery.pa.gov/Custom/ebw/JsWinners.aspx"

# PA's 67 counties (uppercase as required by the endpoint).
PA_COUNTIES = (
    "ADAMS", "ALLEGHENY", "ARMSTRONG", "BEAVER", "BEDFORD", "BERKS", "BLAIR",
    "BRADFORD", "BUCKS", "BUTLER", "CAMBRIA", "CAMERON", "CARBON", "CENTRE",
    "CHESTER", "CLARION", "CLEARFIELD", "CLINTON", "COLUMBIA", "CRAWFORD",
    "CUMBERLAND", "DAUPHIN", "DELAWARE", "ELK", "ERIE", "FAYETTE", "FOREST",
    "FRANKLIN", "FULTON", "GREENE", "HUNTINGDON", "INDIANA", "JEFFERSON",
    "JUNIATA", "LACKAWANNA", "LANCASTER", "LEBANON", "LEHIGH", "LUZERNE",
    "LYCOMING", "MCKEAN", "MERCER", "MIFFLIN", "MONROE", "MONTGOMERY",
    "MONTOUR", "NORTHAMPTON", "NORTHUMBERLAND", "PERRY", "PHILADELPHIA",
    "PIKE", "POTTER", "SCHUYLKILL", "SNYDER", "SOMERSET", "SULLIVAN",
    "SUSQUEHANNA", "TIOGA", "UNION", "VENANGO", "WARREN", "WASHINGTON",
    "WAYNE", "WESTMORELAND", "WYOMING", "YORK",
)

MONTH_NAMES = ("January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December")


def _months_back(today: dt.date, days: int):
    """Yield (year, month_name, month_num) tuples covering the last `days` days,
    most recent first.

    Always yields at least the current month AND the previous full month —
    PA publishes month-by-month, and on the first of June there are zero
    June wins yet, but May is still the freshest real data. The old
    "stop when first_of_month < cutoff" rule skipped May entirely once the
    14d window ended after June 1st, leaving the scraper returning 0 rows
    for ~3 weeks each month."""
    cutoff = today - dt.timedelta(days=days)
    cursor_year, cursor_month = today.year, today.month
    # Always include the current month, then unconditionally include the
    # immediately-prior month, then continue walking back until first-of-month
    # is older than the lookback cutoff.
    yield cursor_year, MONTH_NAMES[cursor_month - 1], cursor_month
    if cursor_month == 1:
        cursor_year -= 1
        cursor_month = 12
    else:
        cursor_month -= 1
    while True:
        yield cursor_year, MONTH_NAMES[cursor_month - 1], cursor_month
        first_of_month = dt.date(cursor_year, cursor_month, 1)
        if first_of_month < cutoff:
            return
        if cursor_month == 1:
            cursor_year -= 1
            cursor_month = 12
        else:
            cursor_month -= 1


class PennsylvaniaWinnersScraper(WinnersScraper):
    state_code = "PA"
    state_name = "Pennsylvania"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        today = dt.date.today()
        out: list[dict] = []
        seen: set[str] = set()
        for year, month_name, month_num in _months_back(today, days):
            for county in PA_COUNTIES:
                items = self._fetch(county, year, month_name)
                if not items:
                    continue
                # Defense against the MO-style "URL silently ignores params" rot:
                # PA tags each row with its own year/month; refuse to ingest a
                # response whose first row's period doesn't match what we asked
                # for. Without this, a broken endpoint would let us re-stamp
                # the same wins into every iterated month (see missouri.py
                # postmortem note).
                first = items[0]
                got_year = first.get("year")
                got_month = (first.get("month") or "").lower()
                if got_year != year or got_month != month_name.lower():
                    logger.warning(
                        "PA %s/%s/%d returned rows tagged %s/%s — skipping batch",
                        county, month_name, year, got_year, got_month,
                    )
                    continue
                for w in items:
                    norm = self._normalize(w, year, month_num)
                    if norm and norm["source_id"] not in seen:
                        seen.add(norm["source_id"])
                        out.append(norm)
                # Polite pacing — PA's endpoint is on a CMS server.
                time.sleep(0.05)
            logger.info("PA %s %d: cumulative %d wins", month_name, year, len(out))
        return out

    def _fetch(self, county: str, year: int, month: str) -> list[dict]:
        try:
            resp = self.get(URL, params={"county": county, "year": year, "month": month})
        except Exception as e:
            logger.warning("PA %s/%s/%d fetch failed: %s", county, month, year, e)
            return []
        text = resp.text.strip()
        if not text or text == "[]":
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return []

    def _normalize(self, w: dict, year: int, month_num: int) -> dict | None:
        amt = (w.get("prize_amount") or "").replace("$", "").replace(",", "").strip()
        try:
            prize = float(amt)
        except ValueError:
            return None
        if prize < self.min_prize:
            return None
        game_name = (w.get("game_name") or "").strip()
        if not game_name:
            return None
        # PA flags scratch via is_instant — anything else (Powerball, Cash 4, etc.) we skip.
        if str(w.get("is_instant")).lower() != "true":
            return None
        if is_draw_game(self.state_code, game_name):
            return None
        winner_city = (w.get("city") or "").strip().title() or None
        if not winner_city:
            return None
        claim_date = _month_end_or_today(year, month_num, dt.date.today())

        # Synth a stable id: PA does not expose a per-win id, but name+city+game+prize+month is unique in practice.
        winner_name = (w.get("name") or "").strip()
        sid_parts = [f"{year:04d}-{month_num:02d}", winner_name, winner_city,
                     game_name, f"{int(prize)}"]
        source_id = "|".join(sid_parts)

        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": None,
            "retailer_city": None,
            "retailer_address": None,
            "retailer_zip": None,
            "winner_city": winner_city,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": "https://www.palottery.pa.gov/About-Lottery/Recent-Winners/",
        }
