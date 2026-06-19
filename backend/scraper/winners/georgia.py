"""
Georgia winners scraper.

GA Lottery publishes a featured-winners gallery as an AEM infinity.json:
  GET https://www.galottery.com/content/portal/en/winners/winners-gallery.infinity.json
Under `jcr:content.body.winnersgallery.winnersgallery[]` — each entry is a
JSON-encoded string with fields: winamount, winnername, winnerlocation,
windate, game, imageUrl.

The feed is mostly historical (latest record ~mid-2024 as of May 2026), so this
provides a one-shot historical pull rather than a fresh-data feed. Retailer
information is NOT exposed — we store winner home city and rely on the
city-centroid geocoder.
"""
from __future__ import annotations
import datetime as dt
import json
import logging
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = ("https://www.galottery.com/en-us/winners/"
       "winners-gallery.infinity.json")


class GeorgiaWinnersScraper(WinnersScraper):
    state_code = "GA"
    state_name = "Georgia"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        today = dt.date.today()
        cutoff = today - dt.timedelta(days=days)

        resp = self.get(URL)
        root = resp.json()
        items = self._find_winners_array(root)
        if not items:
            logger.warning("GA: no winnersgallery array found")
            return []

        out: list[dict] = []
        seen: set[str] = set()
        for raw in items:
            try:
                w = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            norm = self._normalize(w)
            if not norm:
                continue
            if norm["claim_date"] and norm["claim_date"] < cutoff:
                continue
            if norm["source_id"] in seen:
                continue
            seen.add(norm["source_id"])
            out.append(norm)
        return out

    def _find_winners_array(self, root: dict) -> list:
        body = root.get("jcr:content", {}).get("body", {})
        wg = body.get("winnersgallery", {})
        arr = wg.get("winnersgallery")
        if isinstance(arr, list):
            return arr
        if isinstance(arr, str):
            return [arr]
        return []

    def _normalize(self, w: dict) -> dict | None:
        # GA's amount is plain text like "121,386.52" or "52,031,174"
        amt_raw = (w.get("winamount") or "").replace("$", "").replace(",", "").strip()
        try:
            prize = float(amt_raw)
        except (ValueError, TypeError):
            return None
        if prize < self.min_prize:
            return None

        game_raw = (w.get("game") or "").strip().upper()
        # GA's gallery groups entries by `game`:
        #   SCRATCHER  → weekly aggregate totals (no individual winner) — skip
        #   DIGGI, ONLINE → individual digital-instant winners (scratch-like) — keep
        #   MEGAMILLIONS, POWERBALL, FANTASY5 → draw games — skip
        if game_raw not in ("DIGGI", "ONLINE", ""):
            return None
        if is_draw_game(self.state_code, game_raw):
            return None
        # Skip weekly-aggregate names like "Week of March 10, 2024"
        winner_name_raw = (w.get("winnername") or "").strip()
        if winner_name_raw.lower().startswith("week of"):
            return None

        date_str = (w.get("windate") or "").strip()
        claim_date = None
        if date_str:
            try:
                # ISO 8601 with Z
                claim_date = dt.datetime.strptime(date_str[:10], "%Y-%m-%d").date()
            except ValueError:
                pass

        # winnerlocation is e.g. "Greensboro" or "Augusta, GA"
        loc = (w.get("winnerlocation") or "").strip()
        if loc.endswith(", GA"):
            loc = loc[:-4].strip()
        elif loc.lower() == "georgia":
            loc = None
        winner_city = loc.title() if loc else None
        if not winner_city:
            return None

        # GA's winnername is often "Name - March 18, 2024" — keep as-is for uniqueness.
        sid_parts = [
            date_str[:10] if date_str else "",
            winner_name_raw,
            winner_city,
            f"{int(prize)}",
        ]
        source_id = "|".join(sid_parts)

        # Game-name field: use the GA category as a stand-in since exact game isn't published.
        game_display = "Digital Instant" if game_raw == "DIGGI" else (game_raw.title() or "Instant")

        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game_display,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": None,
            "retailer_city": None,
            "retailer_address": None,
            "retailer_zip": None,
            "winner_city": winner_city,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": "https://www.galottery.com/en-us/winners/winners-gallery.html",
        }
