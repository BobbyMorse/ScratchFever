"""
Maryland Lottery scratch-off scraper.
Listing: https://www.mdlottery.com/games/scratch-offs/ (JS-rendered, needs extra wait)
  li.ticket[id^=ticket_] elements contain all data:
    .price, .name, .probability (overall odds)
    #prize_details_{id} table: Prize Amount | Start | Remaining
"""
import re
import logging
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

LIST_URL = "https://www.mdlottery.com/games/scratch-offs/"
BASE_URL = "https://www.mdlottery.com"
PROMOTIONS_URL = "https://www.mdlottery.com/my-lottery-rewards/promotions/"
SECOND_CHANCE_URL = "https://www.mdlottery.com/my-lottery-rewards/promotions/"


def _norm_md_name(s: str) -> str:
    s = s or ""
    s = s.replace("$", "").replace(",", "").replace(".", "")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())


class MarylandScraper(PlaywrightScraper):
    state_code = "MD"
    state_name = "Maryland"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        soup = self.pw_soup(
            LIST_URL, wait_for="load", timeout=45_000, extra_wait_ms=6_000
        )

        sc_names = self._fetch_second_chance_names()
        logger.info("MD second-chance promo games: %d", len(sc_names))

        games = []
        for li in soup.find_all("li", id=re.compile(r"^ticket_\d+")):
            try:
                game_id = re.search(r"(\d+)$", li["id"]).group(1)

                name_el = li.select_one(".name")
                name = name_el.get_text(strip=True) if name_el else ""
                if not name:
                    continue

                price_el = li.select_one(".price")
                price_text = price_el.get_text(strip=True) if price_el else ""
                price = parse_prize_amount(price_text)
                if not price:
                    continue

                prob_el = li.select_one(".probability")
                overall_odds = float(prob_el.get_text(strip=True)) if prob_el else None

                # Prize table is inline as #prize_details_{id}
                prize_div = soup.find(id=f"prize_details_{game_id}")
                tiers = []
                total_prizes_printed = 0
                total_prizes_remaining = 0
                any_remaining_data = False

                if prize_div:
                    table = prize_div.find("table")
                    if table:
                        for row in table.find_all("tr")[1:]:
                            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                            if len(cells) < 3:
                                continue
                            prize = parse_prize_amount(cells[0])
                            try:
                                total = int(cells[1].replace(",", ""))
                                remaining = int(cells[2].replace(",", ""))
                            except (ValueError, IndexError):
                                continue
                            if prize and prize > 0 and total > 0:
                                if remaining > 0:
                                    any_remaining_data = True
                                total_prizes_printed += total
                                total_prizes_remaining += remaining
                                tiers.append({
                                    "prize_amount":     prize,
                                    "odds_one_in":      None,
                                    "prizes_total":     total,
                                    "prizes_remaining": remaining,
                                })

                total_tickets = None
                tickets_remaining = None
                if overall_odds and overall_odds > 0 and total_prizes_printed > 0:
                    total_tickets = round(overall_odds * total_prizes_printed)
                    if any_remaining_data:
                        tickets_remaining = round(overall_odds * total_prizes_remaining)
                    for t in tiers:
                        if t["prizes_total"] and total_tickets:
                            t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

                img_el = li.find("img")
                image_url = img_el.get("src") if img_el else None

                norm = _norm_md_name(name)
                # Match by substring either direction — promo copy says
                # "X the Cash" but a scratcher might be "50X the Cash"; we
                # only flag when the promo name appears as a complete token
                # sequence inside the game name (or vice versa).
                has_2c = any(p == norm or p in norm or norm in p for p in sc_names)
                games.append(self.build_game(
                    game_id=game_id,
                    name=name,
                    price=price,
                    tiers=tiers,
                    overall_odds=overall_odds,
                    total_tickets=total_tickets,
                    tickets_remaining=tickets_remaining,
                    detail_url=f"{LIST_URL}#{li['id']}",
                    image_url=image_url,
                    has_second_chance=has_2c,
                    second_chance_url=SECOND_CHANCE_URL if has_2c else None,
                ))
            except Exception as e:
                logger.debug("MD card parse error: %s", e)

        logger.info("MD: %d games scraped", len(games))
        return games
