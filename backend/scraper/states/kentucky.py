"""
Kentucky Lottery scratch-off scraper.
Listing: https://www.kylottery.com/apps/scratch_offs/
  Active games embedded as inline JS: var cmElement = []; cmElement["SOG-GAMENAME"] = "..."
  Fields: SOG-GAMENAME, SOG-PRICEPOINT, SOG-OTHER-GAME (detail path)
Detail: https://www.kylottery.com/apps/scratch_offs/games/[slug]
  "Overall Odds: 1:X.XX", table: Prize Amount | Prizes Remaining
  tickets_remaining estimated as overall_odds * total_prizes_remaining
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.kylottery.com/apps/scratch_offs/"
BASE_URL = "https://www.kylottery.com"


class KentuckyScraper(BaseScraper):
    state_code = "KY"
    state_name = "Kentucky"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(GAMES_URL)
        text = resp.text

        # Extract active game blocks from inline JS
        blocks = re.findall(
            r'var cmElement = \[\];.*?cmElementsList\.push\(cmElement\);',
            text, re.DOTALL
        )

        games = []
        seen = set()
        for block in blocks:
            fields = dict(re.findall(r'cmElement\["([^"]+)"\]\s*=\s*"([^"]*)"', block))
            name_raw = fields.get("SOG-GAMENAME", "").strip()
            if not name_raw:
                continue
            # Remove trailing " - NNN" game number suffix from name
            name = re.sub(r'\s*-\s*\d+$', '', name_raw).strip()

            try:
                price = float(fields.get("SOG-PRICEPOINT") or 0)
            except (ValueError, TypeError):
                continue
            if not price:
                continue

            detail_path = fields.get("SOG-OTHER-GAME", "")
            if not detail_path:
                continue
            detail_url = BASE_URL + detail_path

            # game id: last segment after _ in path
            m = re.search(r'[/_](\d+)$', detail_path)
            game_id = m.group(1) if m else re.sub(r'[^a-z0-9]', '', name.lower())[:20]

            if game_id in seen:
                continue
            seen.add(game_id)

            tiers, overall_odds, tickets_remaining = self._scrape_detail(detail_url)

            img_path = fields.get("SOG-IMG-ICON", "").strip()
            image_url = (BASE_URL + img_path) if img_path.startswith("/") else (img_path or None)

            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=tiers,
                overall_odds=overall_odds,
                tickets_remaining=tickets_remaining,
                detail_url=detail_url,
                image_url=image_url,
            ))

        logger.info("KY: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str):
        try:
            soup = self.soup(url)
        except Exception:
            return [], None, None

        text = soup.get_text(" ", strip=True)

        # "Overall Odds: 1:3.73"
        overall_odds = None
        m = re.search(r"overall\s+odds[:\s]+1[:\s]+([\d.]+)", text, re.I)
        if m:
            overall_odds = float(m.group(1))

        # Table: Prize Amount | Prizes Remaining
        tiers = []
        total_prizes_remaining = 0
        table = soup.find("table")
        if table:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                prize = parse_prize_amount(cells[0].get_text(strip=True))
                if not prize or prize <= 0:
                    continue
                try:
                    remaining = int(cells[1].get_text(strip=True).replace(",", ""))
                except (ValueError, TypeError):
                    continue
                total_prizes_remaining += remaining
                tiers.append({
                    "prize_amount":     prize,
                    "odds_one_in":      None,
                    "prizes_total":     None,
                    "prizes_remaining": remaining,
                })

        tickets_remaining = None
        if overall_odds and total_prizes_remaining > 0:
            tickets_remaining = round(overall_odds * total_prizes_remaining)
            for t in tiers:
                if t["prizes_remaining"] and tickets_remaining:
                    t["odds_one_in"] = round(tickets_remaining / t["prizes_remaining"], 2)

        return tiers, overall_odds, tickets_remaining
