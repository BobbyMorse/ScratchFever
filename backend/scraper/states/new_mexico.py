"""
New Mexico Lottery scratch-off scraper.
Listing: https://www.nmlottery.com/games/scratchers/
Server-rendered WordPress page. Each game is a section with:
  <h3>Name</h3> and <p>$X</p> for price (in the same parent div),
  inline table: Prize(0) | Approx. Odds 1 in(1) | Approx. # Prizes(2) | Prizes Remaining(3)
  <p>Approximate overall odds...1 in X.XX</p>
Game ID from img src filename (e.g. 682.jpg → "682").
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.nmlottery.com/games/scratchers/"
BASE_URL = "https://www.nmlottery.com"

# NM lists active second-chance promos in the site nav menu of the same
# scratchers page (no extra fetch needed). Each menu item reads
# "<NAME> Second-Chance Promo[tion]" — we extract NAME and match it
# substring-style against scraped scratcher names.
_NM_SC_PROMO_RE = re.compile(
    r'>([A-Z][A-Za-z0-9 ®&é!\-]{2,60}?)\s+Second-Chance\s+(?:Promo|Promotion)<',
)
SECOND_CHANCE_URL = "https://www.nmlottery.com/games/scratchers/"


def _norm_nm_name(s: str) -> str:
    s = s or ""
    s = s.replace("®", "").replace("™", "").replace("$", "").replace(",", "")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())


class NewMexicoScraper(BaseScraper):
    state_code = "NM"
    state_name = "New Mexico"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        resp = self.get(GAMES_URL)
        sc_promo_names = {
            _norm_nm_name(m.group(1))
            for m in _NM_SC_PROMO_RE.finditer(resp.text)
        }
        logger.info("NM second-chance promo names: %s", sorted(sc_promo_names))

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        games = []
        seen = set()

        for h3 in soup.find_all("h3"):
            try:
                name = h3.get_text(strip=True)
                if not name or name in seen:
                    continue

                container = h3.parent

                # Price from <p>$X</p> (only the ticket price — exact pattern "$N")
                price = None
                for p in container.find_all("p"):
                    m = re.match(r"^\$(\d+)$", p.get_text(strip=True))
                    if m:
                        price = float(m.group(1))
                        break
                if not price:
                    continue

                seen.add(name)

                # Overall odds
                overall_odds = None
                for p in container.find_all("p"):
                    om = re.search(r"overall\s+odds.*?1\s+in\s+([\d.]+)", p.get_text(strip=True), re.I)
                    if om:
                        overall_odds = float(om.group(1))
                        break

                # Game ID and image URL from img src filename
                # Image is in the parent container (grandparent of h3), not h3.parent
                img = container.find("img") or container.parent.find("img")
                game_id = None
                image_url = None
                if img:
                    src = img.get("src", "")
                    m = re.search(r"/(\d+)\.(?:jpg|png|webp)", src, re.I)
                    game_id = m.group(1) if m else None
                    if src:
                        image_url = (BASE_URL + src) if src.startswith("/") else src
                if not game_id:
                    gm = re.search(r"[Gg]ame\s*[#No.]+\s*(\d{3,6})", container.get_text())
                    game_id = gm.group(1) if gm else re.sub(r"[^a-z0-9]", "", name.lower())[:20]

                # Prize table: Prize(0) | Odds(1) | Total(2) | Remaining(3)
                tiers = []
                table = container.find("table")
                if table:
                    rows = table.find_all("tr")
                    for row in rows[1:]:
                        cells = row.find_all(["td", "th"])
                        if len(cells) < 2:
                            continue
                        prize = parse_prize_amount(cells[0].get_text(strip=True))
                        odds = parse_odds(cells[1].get_text(strip=True))
                        if not prize or prize <= 0:
                            continue
                        total = rem = None
                        if len(cells) > 2:
                            try:
                                total = int(cells[2].get_text(strip=True).replace(",", ""))
                            except (ValueError, TypeError):
                                pass
                        if len(cells) > 3:
                            try:
                                rem = int(cells[3].get_text(strip=True).replace(",", ""))
                            except (ValueError, TypeError):
                                pass
                        tiers.append({
                            "prize_amount": prize,
                            "odds_one_in": odds,
                            "prizes_remaining": rem,
                            "prizes_total": total,
                        })

                if not tiers and not overall_odds:
                    continue

                total_rem = sum(t.get("prizes_remaining") or 0 for t in tiers)
                total_tot = sum(t.get("prizes_total") or 0 for t in tiers)
                all_have_rem = tiers and all(t.get("prizes_remaining") is not None for t in tiers)
                tickets_remaining = round(overall_odds * total_rem) if overall_odds and all_have_rem else None
                total_tickets = round(overall_odds * total_tot) if overall_odds and total_tot else None

                norm = _norm_nm_name(name)
                # Match when the promo name appears as a contiguous token
                # sequence inside the game name. NM promos use ALL-CAPS
                # branded names ("JURASSIC PARK") that appear as prefix/
                # substring of the scratcher name ("JURASSIC PARK $5").
                has_2c = any(promo and promo in norm for promo in sc_promo_names)
                games.append(self.build_game(
                    game_id=str(game_id),
                    name=name,
                    price=price,
                    tiers=tiers,
                    overall_odds=overall_odds,
                    tickets_remaining=tickets_remaining,
                    total_tickets=total_tickets,
                    image_url=image_url,
                    has_second_chance=has_2c,
                    second_chance_url=SECOND_CHANCE_URL if has_2c else None,
                ))
            except Exception as e:
                logger.debug("NM parse error: %s", e)

        logger.info("NM: %d games scraped", len(games))
        return games
