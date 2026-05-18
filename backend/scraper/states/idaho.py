"""
Idaho Lottery scratch-off scraper.
Post-2026 site redesign: /games/scratch lists all active games.
Detail page /games/scratch/<slug> has full prize table with columns:
  Number of Prizes | Prize Amount | Remaining Prizes | Odds
Also exposes "Tickets Printed" for exact total ticket count.
Small prizes (< $25) show "*not available" for remaining — handled gracefully.
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

BASE_URL = "https://www.idaholottery.com"
LISTING_URL = f"{BASE_URL}/games/scratch"


class IdahoScraper(BaseScraper):
    state_code = "ID"
    state_name = "Idaho"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        slugs = self._get_slugs()
        games = []
        for slug in slugs:
            url = f"{BASE_URL}/games/scratch/{slug}"
            try:
                game = self._scrape_detail(slug, url)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("ID detail failed for %s: %s", slug, e)
        logger.info("ID: %d games scraped", len(games))
        return games

    def _get_slugs(self) -> list[str]:
        soup = self.soup(LISTING_URL)
        seen: set[str] = set()
        slugs: list[str] = []
        for a in soup.find_all("a", href=True):
            m = re.match(r"^/games/scratch/([a-z0-9][a-z0-9-]*)$", a["href"])
            if not m:
                continue
            slug = m.group(1)
            # Skip pure-numeric paths like /1 /2 /10 — those are price filter pages
            if slug.isdigit():
                continue
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)
        return slugs

    def _scrape_detail(self, slug: str, url: str) -> dict | None:
        soup = self.soup(url)
        text = soup.get_text(" ", strip=True)

        # Name: prefer page title over slug
        title_tag = soup.find("title")
        if title_tag:
            raw_title = title_tag.get_text(strip=True)
            name = re.sub(r"\s*\|.*$", "", raw_title).strip() or slug.replace("-", " ").title()
        else:
            h1 = soup.find("h1")
            name = h1.get_text(strip=True) if h1 else slug.replace("-", " ").title()

        # Price and overall odds live in div.section__bar:
        #   "$ 300,000 Top Prize $ 30.00 Ticket 1:2.95 overall odds 57.04 % sold"
        # We must NOT use the full page text because Related Games links embed OTHER
        # games' prices in "$ X.XX | Scratch" format which gets picked up first.
        price = None
        overall_odds = None
        bar = soup.find("div", class_="section__bar")
        bar_text = bar.get_text(" ", strip=True) if bar else ""
        pm = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s+Ticket\b", bar_text, re.I)
        if pm:
            price = float(pm.group(1).replace(",", ""))
        om = re.search(r"1:([\d,]+(?:\.\d+)?)\s+overall\s+odds", bar_text, re.I)
        if om:
            overall_odds = float(om.group(1).replace(",", ""))
        if not price:
            return None

        # Tickets Printed gives us the exact total
        total_tickets = None
        tm = re.search(r"[Tt]ickets?\s+[Pp]rinted[:\s]+([\d,]+)", text)
        if tm:
            total_tickets = int(tm.group(1).replace(",", ""))

        # Image: /sites/default/files/...
        image_url = None
        img = soup.find("img", src=re.compile(r"/sites/default/files/"))
        if img:
            src = img.get("src", "")
            image_url = (BASE_URL + src) if src.startswith("/") else src

        # Game ID: try numeric from image filename, then page text, then slug
        game_id = slug
        if img:
            m = re.search(r"[/_-](\d{3,6})(?:[._-]|$)", img.get("src", ""))
            if m:
                game_id = m.group(1)
        if game_id == slug:
            m = re.search(r"[Gg]ame\s*[#No.]+\s*(\d{3,6})", text)
            if m:
                game_id = m.group(1)

        # Prize table: Number of Prizes | Prize Amount | Remaining Prizes | Odds
        tiers = []
        for table in soup.find_all("table"):
            if "prize" not in table.get_text().lower():
                continue
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            qty_col = prize_col = rem_col = odds_col = None
            for i, h in enumerate(hdrs):
                if "remaining" in h and rem_col is None:
                    rem_col = i
                elif ("number" in h or "qty" in h) and qty_col is None:
                    qty_col = i
                elif ("prize" in h or "amount" in h) and prize_col is None:
                    prize_col = i
                elif "odd" in h and odds_col is None:
                    odds_col = i
            if prize_col is None:
                continue
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= prize_col:
                    continue
                prize = parse_prize_amount(cells[prize_col].get_text(strip=True))
                if not prize or prize <= 0:
                    continue

                odds = None
                if odds_col is not None and len(cells) > odds_col:
                    odds_txt = re.sub(r"^1:", "", cells[odds_col].get_text(strip=True))
                    odds = parse_odds(odds_txt)

                qty = None
                if qty_col is not None and len(cells) > qty_col:
                    try:
                        qty = int(cells[qty_col].get_text(strip=True).replace(",", ""))
                    except (ValueError, TypeError):
                        pass

                rem = None
                if rem_col is not None and len(cells) > rem_col:
                    rem_txt = cells[rem_col].get_text(strip=True).replace(",", "").strip()
                    if not rem_txt.startswith("*") and re.search(r"\d", rem_txt):
                        try:
                            rem = int(re.search(r"\d+", rem_txt).group())
                        except (ValueError, AttributeError):
                            pass

                tiers.append({
                    "prize_amount": prize,
                    "odds_one_in": odds,
                    "prizes_remaining": rem,
                    "prizes_total": qty,
                })
            if tiers:
                break

        if not tiers and not overall_odds:
            return None

        # tickets_remaining: use ratio of remaining/total for tiers that expose both.
        # Small prizes (< $25) don't publish remaining but we use the high-value tier
        # sell-through rate as a proxy for overall sell-through.
        tickets_remaining = None
        if total_tickets:
            paired = [
                (t["prizes_remaining"], t["prizes_total"])
                for t in tiers
                if t["prizes_remaining"] is not None and t["prizes_total"]
            ]
            if paired:
                sum_rem = sum(r for r, _ in paired)
                sum_tot = sum(t for _, t in paired)
                if sum_tot > 0:
                    tickets_remaining = round(total_tickets * sum_rem / sum_tot)
        elif overall_odds:
            total_rem = sum(t["prizes_remaining"] for t in tiers if t["prizes_remaining"] is not None)
            if total_rem:
                tickets_remaining = round(overall_odds * total_rem)

        return self.build_game(
            game_id=slug,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=url,
            image_url=image_url,
        )
