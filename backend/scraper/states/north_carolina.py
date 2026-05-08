"""
North Carolina Education Lottery scratch-off scraper.
Listing: https://nclottery.com/scratch-off  (83 games with price in link text)
Detail:  https://nclottery.com/scratch-off/{id}/{slug}
Table columns: Value | Odds 1 in | Total | Remaining
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

LIST_URL = "https://nclottery.com/scratch-off"
BASE_URL = "https://nclottery.com"


class NorthCarolinaScraper(BaseScraper):
    state_code = "NC"
    state_name = "North Carolina"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(LIST_URL)
        games = []

        # Each game is a link /scratch-off/{id}/{slug} with price in text e.g. "NEW$20" or "$30"
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"/scratch-off/(\d+)/([^/\s?#]+)", href)
            if not m or href in seen:
                continue
            seen.add(href)

            game_id = m.group(1)
            slug = m.group(2)
            text = a.get_text(strip=True)
            price_m = re.search(r"\$(\d+)", text)
            if not price_m:
                continue
            price = float(price_m.group(1))

            name = slug.replace("-", " ").title()
            detail_url = BASE_URL + href if href.startswith("/") else href

            tiers, tickets_remaining, total_tickets = self._scrape_detail(detail_url)

            game = self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=tiers,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                detail_url=detail_url,
            )
            games.append(game)

        return games

    def _scrape_detail(self, url: str) -> tuple[list, int | None, int | None]:
        soup = self.soup(url)
        tiers = []

        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not any(h in " ".join(headers) for h in ("value", "prize", "odds")):
                continue

            # Map columns: Value=prize, Odds 1 in=odds, Total=total, Remaining=remaining
            col = {}
            for i, h in enumerate(headers):
                if "value" in h or "prize" in h or "amount" in h:
                    col["prize"] = i
                elif "odd" in h:
                    col["odds"] = i
                elif "remaining" in h or "left" in h:
                    col["remaining"] = i
                elif "total" in h:
                    col["total"] = i

            rows = table.find_all("tr")[1:]  # skip header
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if not cells or len(cells) < 2:
                    continue
                # Skip disclaimer rows (cells that span multiple columns or start with *)
                if len(cells) == 1 or cells[0].startswith("*"):
                    continue

                prize_idx = col.get("prize", 0)
                odds_idx = col.get("odds", 1)
                rem_idx = col.get("remaining")
                tot_idx = col.get("total")

                prize = parse_prize_amount(cells[prize_idx]) if prize_idx < len(cells) else None
                odds = parse_odds(cells[odds_idx]) if odds_idx < len(cells) else None
                remaining = None
                total = None

                if rem_idx is not None and rem_idx < len(cells):
                    try:
                        remaining = int(cells[rem_idx].replace(",", ""))
                    except ValueError:
                        pass
                if tot_idx is not None and tot_idx < len(cells):
                    try:
                        total = int(cells[tot_idx].replace(",", ""))
                    except ValueError:
                        pass

                if prize and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds,
                        "prizes_remaining": remaining,
                        "prizes_total": total,
                    })
            if tiers:
                break

        total_tickets = None
        tickets_remaining = None
        if tiers:
            refs = [(t.get("prizes_total") or 0, t.get("odds_one_in") or 0) for t in tiers]
            refs = [(tot, odds) for tot, odds in refs if tot > 0 and odds > 0]
            if refs:
                estimates = sorted(int(tot * odds) for tot, odds in refs)
                total_tickets = estimates[len(estimates) // 2]

            if total_tickets:
                sum_total = sum(t.get("prizes_total") or 0 for t in tiers)
                sum_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
                if sum_total > 0:
                    tickets_remaining = round(total_tickets * sum_remaining / sum_total)

        return tiers, tickets_remaining, total_tickets
