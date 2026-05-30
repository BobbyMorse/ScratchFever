"""
Texas winners scraper.

TX Lottery's news release index lists each $1M+ winner press release as a
linked PDF. Title format:
  "<CITY> RESIDENT CLAIMS $<AMT> SCRATCH TICKET PRIZE"
  "<CITY> RESIDENT CLAIMS SHARE OF $<AMT> POWERBALL® JACKPOT"
PDF filename format:
  MM-DD-YY_<City>[-<RetailerCity>]_<AMT>_<GameName>_-_News_Release.pdf

We parse straight from the index listing — title gives us city + scratch flag,
filename gives us date + game name. This is a $1M+ floor by nature of what TX
publishes press releases for. No retailer info exposed here (would need PDF parse).
"""
from __future__ import annotations
import datetime as dt
import logging
import re
from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.texaslottery.com/export/sites/lottery/Media/News_Releases/index.html"

LINK_RE = re.compile(
    r'<a[^>]+href="([^"]+(\d{2}-\d{2}-\d{2})[_-]([^"]+?)\.pdf)"[^>]*>([^<]+)</a>',
    re.IGNORECASE,
)
AMOUNT_RE = re.compile(r'\$?([\d,.]+)\s*(MILLION|M|THOUSAND|K)?', re.IGNORECASE)


def _parse_amount(num: str, unit: str | None) -> float | None:
    try:
        n = float(num.replace(",", ""))
    except (ValueError, TypeError):
        return None
    u = (unit or "").upper()
    if u in ("MILLION", "M"):
        return n * 1_000_000
    if u in ("THOUSAND", "K"):
        return n * 1_000
    return n


class TexasWinnersScraper(WinnersScraper):
    state_code = "TX"
    state_name = "Texas"
    min_prize = 1_000_000.0  # TX only press-releases $1M+ wins

    def scrape(self, days: int = 14) -> list[dict]:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        resp = self.get(URL)
        out: list[dict] = []
        seen: set[str] = set()
        for m in LINK_RE.finditer(resp.text):
            href, date_raw, slug, title = m.groups()
            title = title.strip()
            # Skip non-winner releases (raises, etc.)
            if "CLAIMS" not in title.upper() and "WINS" not in title.upper():
                continue
            try:
                claim_date = dt.datetime.strptime(date_raw, "%m-%d-%y").date()
            except ValueError:
                continue
            if claim_date < cutoff:
                continue
            # Extract winner home city from title
            mt = re.match(r'^([A-Z][A-Z\s\.\-]+?)\s+RESIDENT\s+CLAIMS', title, re.IGNORECASE)
            city = mt.group(1).strip().title() if mt else None
            if not city:
                # Try "<CITY> COUPLE" / "<CITY> MAN" / "<CITY> WOMAN"
                mt = re.match(r'^([A-Z][A-Z\s\.\-]+?)\s+(?:COUPLE|MAN|WOMAN|FAMILY)\s+', title, re.IGNORECASE)
                city = mt.group(1).strip().title() if mt else None
            if not city:
                continue
            # Pull amount from title — first $-marker is the prize
            ma = re.search(r'\$([\d,.]+)\s*(MILLION|M|THOUSAND|K)?', title, re.IGNORECASE)
            prize = _parse_amount(ma.group(1), ma.group(2)) if ma else None
            if not prize or prize < self.min_prize:
                continue
            # Extract game name from filename slug
            # Slug looks like "Leander_20M_Powerball_Jackpot_Claimed_-_News_Release"
            game = self._extract_game_from_slug(slug)
            is_scratch = "SCRATCH" in title.upper()
            if not is_scratch and is_draw_game(self.state_code, game):
                continue
            sid_parts = [date_raw, city, f"{int(prize)}", game]
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
                "winner_city": city,
                "retailer_lat": None,
                "retailer_lng": None,
                "source_url": "https://www.texaslottery.com" + href if href.startswith("/") else href,
            })
        return out

    @staticmethod
    def _extract_game_from_slug(slug: str) -> str:
        # "Leander_20M_Powerball_Jackpot_Claimed_-_News_Release"
        # Strip trailing "_-_News_Release" and known suffixes; the middle word(s)
        # after the amount is the game.
        s = slug.replace("_-_News_Release", "").replace("_News_Release", "")
        parts = s.split("_")
        # Drop city, amount token; keep the rest until "Claimed"/"Jackpot"/etc.
        out: list[str] = []
        amount_seen = False
        for p in parts:
            if not amount_seen:
                if re.match(r'^\$?\d+(\.\d+)?[MK]?$', p, re.IGNORECASE):
                    amount_seen = True
                continue
            if p.lower() in ("claimed", "wins", "winner", "winning"):
                break
            out.append(p)
        game = " ".join(out).strip()
        return game or "Scratch Ticket"
