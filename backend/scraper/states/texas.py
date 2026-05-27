"""
Texas Lottery scratch-off scraper.
Listing: https://www.texaslottery.com/export/sites/lottery/Games/Scratch_Offs/all.html
Detail:  https://www.texaslottery.com/export/sites/lottery/Games/Scratch_Offs/details.html_{id}.html
CSV:     https://www.texaslottery.com/export/sites/lottery/Games/Scratch_Offs/scratchoff.csv
  CSV columns: Game Number, Game Name, Game Close Date, Ticket Price, Prize Level,
               Total Prizes in Level, Prizes Claimed

Strategy:
  1. Scrape all.html to build game_num → detail_url mapping.
  2. Parse CSV for prize tiers (printed / claimed counts).
  3. Fetch each detail page for total_tickets and overall_odds.
  4. Estimate tickets_remaining = total_tickets × (prizes_remaining / prizes_printed).
  5. EV computed via remaining-based formula; ev_approximate=True.
"""
from __future__ import annotations
import csv
import io
import re
import logging
from collections import defaultdict
from datetime import date, datetime
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

CSV_URL = "https://www.texaslottery.com/export/sites/lottery/Games/Scratch_Offs/scratchoff.csv"
ALL_URL = "https://www.texaslottery.com/export/sites/lottery/Games/Scratch_Offs/all.html"
BASE_URL = "https://www.texaslottery.com"
DETAIL_BASE = f"{BASE_URL}/export/sites/lottery/Games/Scratch_Offs"


class TexasScraper(BaseScraper):
    state_code = "TX"
    state_name = "Texas"
    base_url = BASE_URL
    scraper_timeout = 600  # ~50 games × detail page fetch each

    def scrape(self) -> list[dict]:
        detail_urls = self._get_detail_urls()
        logger.info("TX: found %d detail URLs from all.html", len(detail_urls))

        resp = self.get(CSV_URL)
        lines = resp.text.splitlines()

        # First line is metadata, second line is actual header
        reader = csv.reader(io.StringIO("\n".join(lines[1:])))
        next(reader)  # skip header row

        games_raw: dict[str, list] = defaultdict(list)
        for row in reader:
            if len(row) < 7:
                continue
            game_num = row[0].strip()
            if not game_num:
                continue
            games_raw[game_num].append(row)

        logger.info("TX: %d games in CSV", len(games_raw))

        games = []
        for game_num, rows in games_raw.items():
            game = self._parse_game(game_num, rows, detail_urls.get(game_num))
            if game:
                games.append(game)

        logger.info("TX: %d games parsed", len(games))
        return games

    def _get_detail_urls(self) -> dict[str, str]:
        """Parse all.html table rows to extract game_num → detail page URL."""
        soup = self.soup(ALL_URL)
        result: dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            game_num = cells[0].get_text(strip=True).replace(",", "")
            if not game_num.isdigit():
                continue
            for cell in cells:
                a = cell.find("a", href=True)
                if a and "details.html_" in (a.get("href") or ""):
                    href = a["href"]
                    full_url = (BASE_URL + href) if href.startswith("/") else href
                    result[game_num] = full_url
                    break
        return result

    def _get_detail_info(self, game_num: str, url: str) -> tuple[int | None, float | None, str | None]:
        """Fetch detail page and return (total_tickets, overall_odds_one_in, image_url)."""
        try:
            from bs4 import BeautifulSoup
            resp = self.get(url)
            text = resp.text

            total_tickets = None
            m = re.search(r"approximately\s+([\d,]+)\*?\s+tickets", text, re.IGNORECASE)
            if m:
                total_tickets = int(m.group(1).replace(",", ""))

            overall_odds = None
            m = re.search(r"overall\s+odds[^.]*?1\s+in\s+([\d.]+)", text, re.IGNORECASE)
            if m:
                overall_odds = float(m.group(1))

            # Ticket art lives at /Images/scratchoffs/{game_num}_img1.(gif|png|jpg)
            image_url = None
            soup = BeautifulSoup(text, "lxml")
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src", "")
                if src and f"scratchoffs/{game_num}_img1" in src:
                    image_url = (BASE_URL + src) if src.startswith("/") else src
                    break

            return total_tickets, overall_odds, image_url
        except Exception as exc:
            logger.warning("TX: detail fetch failed for %s: %s", url, exc)
            return None, None, None

    def _parse_game(self, game_num: str, rows: list, detail_url: str | None) -> dict | None:
        if not rows:
            return None

        name = rows[0][1].strip()
        if not name:
            return None

        # Skip expired games
        end_date = None
        raw_close = rows[0][2].strip() if len(rows[0]) > 2 else ""
        if raw_close:
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
                try:
                    parsed = datetime.strptime(raw_close, fmt).date()
                    if parsed < date.today():
                        return None
                    end_date = parsed.isoformat()
                    break
                except ValueError:
                    continue

        try:
            price = float(rows[0][3])
        except (ValueError, IndexError):
            return None
        if price <= 0:
            return None

        tiers = []
        prizes_printed_sum = 0
        prizes_remaining_sum = 0

        for row in rows:
            level = row[4].strip()
            if level.upper() == "TOTAL":
                continue
            try:
                prize = float(level)
                total = int(row[5].replace(",", ""))
                claimed = int(row[6].replace(",", ""))
            except (ValueError, IndexError):
                continue
            if prize <= 0 or total <= 0:
                continue

            remaining = max(total - claimed, 0)
            prizes_printed_sum += total
            prizes_remaining_sum += remaining

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      None,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers:
            return None

        total_tickets, overall_odds, image_url = None, None, None
        tickets_remaining = None

        if detail_url:
            total_tickets, overall_odds, image_url = self._get_detail_info(detail_url)

        if total_tickets and prizes_printed_sum > 0:
            depletion = prizes_remaining_sum / prizes_printed_sum
            tickets_remaining = round(total_tickets * depletion)

        return self.build_game(
            game_id=game_num,
            name=name,
            price=price,
            tiers=tiers,
            tickets_remaining=tickets_remaining,
            total_tickets=total_tickets,
            overall_odds=overall_odds,
            detail_url=detail_url or DETAIL_BASE,
            end_date=end_date,
            ev_approximate=bool(total_tickets),
            image_url=image_url,
        )
