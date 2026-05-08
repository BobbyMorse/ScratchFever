"""
DC Lottery (District of Columbia) scratch-off scraper.
Listing: https://dclottery.com/games/scratchers
DC publishes odds and prize data on individual game pages.
"""
import re
import json
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://dclottery.com/dc-scratchers/"
BASE_URL = "https://dclottery.com"


class DCScraper(BaseScraper):
    state_code = "DC"
    state_name = "District of Columbia"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        items = soup.select(".scratcher, .game-card, .ticket, .scratch-game, [data-game]")
        if not items:
            items = soup.select("article, .game")

        for item in items:
            try:
                link = item.find("a", href=True)
                name_el = item.select_one(".game-name, .gameName, h3, h4, .name, .title")
                price_el = item.select_one(".price, .ticket-price, [data-price]")

                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                if not name:
                    continue

                price_txt = price_el.get_text(strip=True) if price_el else ""
                if not price_txt:
                    m = re.search(r"\$(\d+)", item.get_text())
                    price_txt = "$" + m.group(1) if m else ""
                price = parse_prize_amount(price_txt)
                if not price:
                    continue

                href = link.get("href", "") if link else ""
                detail_url = (BASE_URL + href) if href.startswith("/") else href or None
                if detail_url in seen:
                    continue
                seen.add(detail_url or name)

                m_id = re.search(r"/(\d+|[a-z0-9-]+)/?$", href)
                game_id = m_id.group(1) if m_id else re.sub(r"[^a-z0-9]", "", name.lower())[:20]

                tiers = []
                node_id = None
                overall_odds = None
                if detail_url:
                    try:
                        tiers, node_id, overall_odds = self._scrape_detail(detail_url)
                    except Exception as e:
                        logger.debug("DC detail failed for %s: %s", name, e)

                if node_id:
                    game_id = node_id

                # Derive ticket counts from overall odds + prize totals
                tickets_remaining = None
                total_tickets = None
                if overall_odds and tiers:
                    total_prizes = sum(t.get("prizes_total") or 0 for t in tiers)
                    remaining_prizes = sum(t.get("prizes_remaining") or 0 for t in tiers)
                    if total_prizes > 0:
                        total_tickets = round(overall_odds * total_prizes)
                        for t in tiers:
                            if t.get("prizes_total"):
                                t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)
                    if remaining_prizes > 0:
                        tickets_remaining = round(overall_odds * remaining_prizes)

                games.append(self.build_game(
                    game_id=str(game_id),
                    name=name,
                    price=price,
                    tiers=tiers,
                    tickets_remaining=tickets_remaining,
                    total_tickets=total_tickets,
                    overall_odds=overall_odds,
                    detail_url=detail_url,
                ))
            except Exception as e:
                logger.debug("DC item parse error: %s", e)

        return games

    def _scrape_detail(self, url: str) -> tuple[list[dict], str | None, float | None]:
        soup = self.soup(url)
        page_text = soup.get_text(" ", strip=True)

        node_id = None
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string or "")
                path = data.get("path", {}).get("currentPath", "")
                m = re.match(r"^node/(\d+)$", path)
                if m:
                    node_id = m.group(1)
                    break
            except Exception:
                pass

        # Extract overall odds — DC shows "Odds 1:3.99"; take the smallest value found
        # (overall odds are small; top prize odds like "Top Prize Odds 1:61,200" are large)
        overall_odds = None
        odds_vals = [
            float(v.replace(",", ""))
            for v in re.findall(r"\bodds\b[^0-9]*1\s*[:\s]+([\d,]+\.?\d*)", page_text, re.I)
            if v.replace(",", "").replace(".", "").isdigit() or "." in v
        ]
        if odds_vals:
            candidate = min(odds_vals)
            if candidate < 100:  # overall odds should be small (1 in 3-10 range)
                overall_odds = candidate

        tiers = []
        for table in soup.find_all("table"):
            text = table.get_text().lower()
            if any(k in text for k in ("prize", "odds", "1 in")):
                tiers = self.parse_table_tiers(table)
                if tiers:
                    break
        return tiers, node_id, overall_odds
