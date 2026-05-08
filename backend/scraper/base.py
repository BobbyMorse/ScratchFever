import logging
import re
from abc import ABC, abstractmethod

import requests
from bs4 import BeautifulSoup

from backend.ev_calculator import calculate_ev, find_top_prize, parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class BaseScraper(ABC):
    state_code: str = ""
    state_name: str = ""
    base_url: str = ""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.timeout = 30

    def get(self, url: str, **kwargs) -> requests.Response:
        resp = self.session.get(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def soup(self, url: str, **kwargs) -> BeautifulSoup:
        resp = self.get(url, **kwargs)
        return BeautifulSoup(resp.text, "lxml")

    def build_game(self, game_id: str, name: str, price: float, tiers: list[dict],
                   tickets_remaining: int = None, total_tickets: int = None,
                   detail_url: str = None, overall_odds: float = None,
                   image_url: str = None) -> dict:
        ev_data = calculate_ev(price, tiers, tickets_remaining)
        top_prize, top_prize_remaining = find_top_prize(tiers)
        return {
            "game_id": str(game_id),
            "name": name,
            "price": price,
            "ev": ev_data["ev"],
            "return_pct": ev_data["return_pct"],
            "overall_odds_one_in": overall_odds,
            "top_prize": top_prize,
            "top_prize_remaining": top_prize_remaining,
            "total_tickets": total_tickets,
            "tickets_remaining": tickets_remaining,
            "detail_url": detail_url,
            "image_url": image_url,
            "tiers": tiers,
        }

    @abstractmethod
    def scrape(self) -> list[dict]:
        """Return list of game dicts (output of build_game)."""

    def safe_scrape(self) -> tuple[list[dict], str | None]:
        try:
            games = self.scrape()
            logger.info("%s: scraped %d games", self.state_code, len(games))
            return games, None
        except Exception as exc:
            logger.error("%s: scrape failed: %s", self.state_code, exc, exc_info=True)
            return [], str(exc)

    # ── helpers ────────────────────────────────────────────────────────────────

    def parse_table_tiers(self, table) -> list[dict]:
        """Generic parser for HTML prize tables.
        Looks for columns: prize, odds, total, remaining.
        """
        tiers = []
        rows = table.find_all("tr")
        header = rows[0] if rows else None
        col_map = {}
        if header:
            cells = header.find_all(["th", "td"])
            for i, cell in enumerate(cells):
                text = cell.get_text(strip=True).lower()
                # Check remaining/total/odds BEFORE prize so "Prizes Remaining"
                # doesn't get mapped to the prize column.
                if "remaining" in text or "left" in text or "unclaimed" in text:
                    col_map["remaining"] = i
                elif "total" in text or "print" in text:
                    col_map["total"] = i
                elif any(k in text for k in ("odd", "chance", "1 in", "probability")):
                    col_map["odds"] = i
                elif any(k in text for k in ("prize", "amount", "award", "win")):
                    col_map["prize"] = i

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            try:
                prize_idx = col_map.get("prize", 0)
                odds_idx = col_map.get("odds", 1)
                prize = parse_prize_amount(cells[prize_idx].get_text(strip=True))
                odds = parse_odds(cells[odds_idx].get_text(strip=True))

                remaining = None
                total_from_rem = None
                if "remaining" in col_map and len(cells) > col_map["remaining"]:
                    rem_txt = cells[col_map["remaining"]].get_text(strip=True).replace(",", "")
                    m = re.match(r"(\d+)\s+of\s+(\d+)", rem_txt, re.IGNORECASE)
                    if m:
                        remaining = int(m.group(1))
                        total_from_rem = int(m.group(2))
                    else:
                        try:
                            remaining = int(float(rem_txt))
                        except (ValueError, TypeError):
                            pass

                total = None
                if "total" in col_map and len(cells) > col_map["total"]:
                    total_txt = cells[col_map["total"]].get_text(strip=True).replace(",", "")
                    try:
                        total = int(float(total_txt))
                    except (ValueError, TypeError):
                        pass
                if total is None:
                    total = total_from_rem

                if prize is not None and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds,
                        "prizes_remaining": remaining,
                        "prizes_total": total,
                    })
            except (IndexError, AttributeError):
                continue

        return tiers
