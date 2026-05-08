"""
Minnesota Lottery scratch-off scraper.
Listing: https://www.mnlottery.com/games/scratch/
Cards use: h2.card--lottery-headline (name), h3.lottery-details (price), [data-game-id] (container)
Detail pages have a bulletin-matrix-table with "To Win / Odds* / Number of Prizes**" columns.
The table cells include accessibility spans that repeat the column header — strip those first.
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.mnlottery.com/games/scratch/"
BASE_URL = "https://www.mnlottery.com"


class MinnesotaScraper(BaseScraper):
    state_code = "MN"
    state_name = "Minnesota"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        items = soup.select("[data-game-id]")

        for item in items:
            try:
                name_el = item.select_one(".card--lottery-headline")
                price_el = item.select_one(".lottery-details")
                link = item.select_one("a[href*='/games/scratch/']")

                if not name_el or not link:
                    continue
                name = name_el.get_text(strip=True)
                if not name:
                    continue

                price_txt = price_el.get_text(strip=True) if price_el else ""
                price = parse_prize_amount(price_txt)
                if not price:
                    m = re.search(r"\$(\d+)", item.get_text())
                    price = float(m.group(1)) if m else None
                if not price:
                    continue

                href = link.get("href", "")
                detail_url = (BASE_URL + href) if href.startswith("/") else href or None
                if detail_url in seen:
                    continue
                seen.add(detail_url or name)

                m_id = re.search(r"/([a-z0-9-]+)/?$", href)
                game_id = m_id.group(1) if m_id else re.sub(r"[^a-z0-9]", "", name.lower())[:20]

                tiers, tickets_remaining, total_tickets = [], None, None
                if detail_url:
                    try:
                        tiers, tickets_remaining, total_tickets = self._scrape_detail(detail_url)
                    except Exception as e:
                        logger.debug("MN detail failed for %s: %s", name, e)

                games.append(self.build_game(
                    game_id=str(game_id),
                    name=name,
                    price=price,
                    tiers=tiers,
                    tickets_remaining=tickets_remaining,
                    total_tickets=total_tickets,
                    detail_url=detail_url,
                ))
            except Exception as e:
                logger.debug("MN item parse error: %s", e)

        return games

    def _scrape_detail(self, url: str) -> tuple[list, int | None, int | None]:
        soup = self.soup(url)

        # MN cells contain <span class="bulletin-matrix-table__label"> that repeat
        # the column header inside each data cell — remove them before parsing.
        for span in soup.find_all("span", class_="bulletin-matrix-table__label"):
            span.decompose()

        tiers = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_cells = rows[0].find_all(["th", "td"])
            headers = [c.get_text(strip=True).lower() for c in header_cells]

            # Confirm this is the prize/odds table
            has_prize = any("win" in h and "number" not in h for h in headers)
            has_odds = any("odd" in h for h in headers)
            if not (has_prize and has_odds):
                continue

            # Map columns: "To Win" → prize, "Odds*" → odds, "Number of Prizes" → total
            prize_col = next((i for i, h in enumerate(headers) if "win" in h and "number" not in h), 0)
            odds_col = next((i for i, h in enumerate(headers) if "odd" in h), 1)
            total_col = next((i for i, h in enumerate(headers) if "number" in h or "total" in h), None)

            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= max(prize_col, odds_col):
                    continue
                prize = parse_prize_amount(cells[prize_col].get_text(strip=True))
                odds = parse_odds(cells[odds_col].get_text(strip=True))
                total = None
                if total_col is not None and len(cells) > total_col:
                    try:
                        total = int(cells[total_col].get_text(strip=True).replace(",", ""))
                    except (ValueError, TypeError):
                        pass
                if prize is not None and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds,
                        "prizes_total": total,
                        "prizes_remaining": None,
                    })
            if tiers:
                break

        page_text = soup.get_text(" ", strip=True)
        m_total = re.search(r"(\d[\d,]+)\s*total\s*tickets?", page_text, re.I)
        total_tickets = int(m_total.group(1).replace(",", "")) if m_total else None
        m_rem = re.search(r"(\d[\d,]+)\s*tickets?\s*(?:still\s*)?(?:remaining|available)", page_text, re.I)
        tickets_remaining = int(m_rem.group(1).replace(",", "")) if m_rem else None

        return tiers, tickets_remaining, total_tickets
