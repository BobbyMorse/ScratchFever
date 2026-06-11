"""
Colorado Lottery scratch-off scraper.

List page: https://www.coloradolottery.com/en/games/scratch/
  - Links like /en/games/scratch/game/{slug}/
Game page: https://www.coloradolottery.com/en/games/scratch/game/{slug}/
  - Prize table: Prize Amount | Winning Tickets | Probability
Scratch Insider: https://www.coloradolottery.com/en/player-tools/scratch-insider/
  - Top prizes remaining per game

Colorado does not publish per-tier remaining counts. EV is approximated by
treating the top-prize depletion rate as a proxy for overall ticket depletion:
  depletion = (top_total - top_remaining) / top_total
  tickets_remaining ≈ total_tickets * (1 - depletion)
  prizes_remaining_i ≈ prizes_total_i * (1 - depletion)
ev_approximate is set True on all CO games so the UI can flag this.
"""
from __future__ import annotations
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

LIST_URL = "https://www.coloradolottery.com/en/games/scratch/"
INSIDER_URL = "https://www.coloradolottery.com/en/player-tools/scratch-insider/"
BASE_URL = "https://www.coloradolottery.com"


class ColoradoScraper(BaseScraper):
    state_code = "CO"
    state_name = "Colorado"
    base_url = BASE_URL
    scraper_timeout = 300

    def scrape(self) -> list[dict]:
        insider = self._scrape_insider()
        logger.info("CO: Scratch Insider data for %d games", len(insider))

        soup = self.soup(LIST_URL)
        seen: set[str] = set()
        game_slugs: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/en/games/scratch/game/" in href:
                m = re.search(r"/game/([^/?#]+)/?", href)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    game_slugs.append(m.group(1))

        logger.info("CO: found %d game slugs", len(game_slugs))

        games = []
        for slug in game_slugs:
            url = f"{BASE_URL}/en/games/scratch/game/{slug}/"
            try:
                game = self._scrape_game(slug, url, insider)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("CO game scrape failed %s: %s", slug, e)

        return games

    # ── Scratch Insider ────────────────────────────────────────────────────────

    def _scrape_insider(self) -> dict[str, dict]:
        """
        Returns {slug: {"remaining": N, "total": M or None}}.
        Tries table rows first, then card/div-based layouts.
        """
        try:
            soup = self.soup(INSIDER_URL)
        except Exception as e:
            logger.warning("CO: Scratch Insider fetch failed: %s", e)
            return {}

        result: dict[str, dict] = {}

        # Strategy 1: table rows containing a link to a game page
        for row in soup.find_all("tr"):
            a = row.find("a", href=re.compile(r"/en/games/scratch/game/"))
            if not a:
                continue
            m = re.search(r"/game/([^/?#]+)/?", a["href"])
            if not m:
                continue
            slug = m.group(1)
            if slug in result:
                continue

            text = row.get_text(" ", strip=True)
            # "3 of 24" pattern
            mo = re.search(r"(\d[\d,]*)\s+of\s+(\d[\d,]*)", text)
            if mo:
                result[slug] = {
                    "remaining": int(mo.group(1).replace(",", "")),
                    "total": int(mo.group(2).replace(",", "")),
                }
                continue

            # Bare numbers in cells — take last two as (total, remaining) or just remaining
            nums = []
            for td in row.find_all(["td", "th"]):
                t = td.get_text(strip=True).replace(",", "")
                try:
                    nums.append(int(float(t)))
                except ValueError:
                    pass
            if nums:
                result[slug] = {
                    "remaining": nums[-1],
                    "total": nums[-2] if len(nums) >= 2 else None,
                }

        # Strategy 2: card/div layout — find any link to a game, look in its container
        if not result:
            for a in soup.find_all("a", href=re.compile(r"/en/games/scratch/game/")):
                m = re.search(r"/game/([^/?#]+)/?", a["href"])
                if not m:
                    continue
                slug = m.group(1)
                if slug in result:
                    continue
                container = a.find_parent(["article", "li", "div"])
                if not container:
                    continue
                text = container.get_text(" ", strip=True)
                mo = re.search(r"(\d[\d,]*)\s+of\s+(\d[\d,]*)", text)
                if mo:
                    result[slug] = {
                        "remaining": int(mo.group(1).replace(",", "")),
                        "total": int(mo.group(2).replace(",", "")),
                    }
                    continue
                # Any standalone small integers in the container
                nums = [int(n) for n in re.findall(r"\b(\d{1,4})\b", text)]
                if nums:
                    result[slug] = {"remaining": nums[-1], "total": None}

        logger.info("CO: Scratch Insider parsed %d entries", len(result))
        return result

    # ── Game detail ────────────────────────────────────────────────────────────

    def _scrape_game(self, slug: str, url: str, insider: dict) -> dict | None:
        soup = self.soup(url)

        # Name
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""
        if not name:
            title = soup.find("title")
            if title:
                name = title.get_text(strip=True).split("|")[0].strip()
        if not name:
            name = re.sub(r"-\d{4}$", "", slug).replace("-", " ").title()

        # Price
        price_el = soup.select_one("p.price, strong.price, .price")
        price = parse_prize_amount(price_el.get_text(strip=True)) if price_el else None
        if not price:
            text = soup.get_text()
            for pat in [r"\$(\d+)\s+(?:scratch|ticket|game)", r"(?:ticket\s+)?price[:\s]+\$?(\d+)"]:
                m = re.search(pat, text, re.I)
                if m:
                    price = float(m.group(1))
                    break
        if not price:
            return None

        # Overall odds
        text = soup.get_text()
        overall_odds = None
        m = re.search(r"overall odds.*?1\s*in\s*([\d,.]+)", text, re.I)
        if m:
            overall_odds = parse_odds(m.group(1))

        # Prize table: Prize Amount | Winning Tickets | Probability
        tiers: list[dict] = []
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not any("prize" in h or "probability" in h or "winning" in h for h in headers):
                continue

            col: dict[str, int] = {}
            for i, h in enumerate(headers):
                if "prize" in h and "amount" in h:
                    col["prize"] = i
                elif "winning" in h or "ticket" in h:
                    col["total"] = i
                elif "probability" in h or "odds" in h or "1 in" in h:
                    col["odds"] = i

            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue
                pi = col.get("prize", 0)
                oi = col.get("odds", 2)
                ti = col.get("total", 1)
                prize = parse_prize_amount(cells[pi]) if pi < len(cells) else None
                odds = parse_odds(cells[oi]) if oi < len(cells) else None
                total = None
                if ti < len(cells):
                    try:
                        total = int(cells[ti].replace(",", ""))
                    except ValueError:
                        pass
                if prize and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "odds_one_in": odds,
                        "prizes_remaining": None,
                        "prizes_total": total,
                    })
            if tiers:
                break

        if not tiers:
            return None

        # ── Apply Scratch Insider approximation ───────────────────────────────
        tickets_remaining = None
        total_tickets = None
        ev_approximate = False

        insider_entry = insider.get(slug)
        if insider_entry is not None:
            top_tier = max(tiers, key=lambda t: t["prize_amount"])
            top_total_prize = top_tier.get("prizes_total")

            # Use insider's "total" if the prize table didn't have it
            insider_total = insider_entry.get("total")
            if not top_total_prize and insider_total:
                top_total_prize = insider_total

            top_remaining = insider_entry["remaining"]

            if top_total_prize and top_total_prize > 0 and top_remaining is not None:
                # When the top prize is fully exhausted the depletion proxy
                # breaks: the game is still on sale with lower-tier prizes,
                # but (top_total - 0) / top_total = 1.0 collapses every
                # tier's prizes_remaining and tickets_remaining to 0. That
                # produces "tickets_remaining=0" in the DB (the Zero Bug)
                # for games that are not actually sold out. Leave inventory
                # unknown in that case.
                if top_remaining == 0:
                    ev_approximate = True
                    logger.debug(
                        "CO %s: top prize exhausted (0/%d) — leaving inventory unknown",
                        slug, top_total_prize,
                    )
                else:
                    depletion = max(0.0, min(1.0, (top_total_prize - top_remaining) / top_total_prize))

                    # Estimate prizes_remaining for every tier proportionally
                    for tier in tiers:
                        pt = tier.get("prizes_total") or 0
                        tier["prizes_remaining"] = max(0, round(pt * (1.0 - depletion)))

                    # Estimate total tickets and tickets_remaining
                    total_prizes_printed = sum(t.get("prizes_total") or 0 for t in tiers)
                    if overall_odds and total_prizes_printed > 0:
                        total_tickets = round(total_prizes_printed * overall_odds)
                    elif top_tier.get("odds_one_in") and top_total_prize:
                        total_tickets = round(top_total_prize * top_tier["odds_one_in"])

                    if total_tickets:
                        tickets_remaining = max(0, round(total_tickets * (1.0 - depletion)))

                    ev_approximate = True
                    logger.debug(
                        "CO %s: depletion=%.1f%% top_rem=%d/%d tickets_rem=%s",
                        slug, depletion * 100, top_remaining, top_total_prize, tickets_remaining,
                    )

        image_url = None
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src", "")
            if src and re.search(r"\.(jpg|jpeg|png|webp)", src, re.I):
                image_url = (BASE_URL + src) if src.startswith("/") else src
                break

        return self.build_game(
            game_id=slug,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            tickets_remaining=tickets_remaining,
            total_tickets=total_tickets,
            detail_url=url,
            ev_approximate=ev_approximate,
            image_url=image_url,
        )
