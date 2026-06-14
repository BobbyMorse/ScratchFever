"""
Louisiana Lottery scratch-off scraper.

Second-chance: LA runs branded second-chance promos that each enumerate
eligible scratchers on the promo's own page (e.g. Golden Nugget, Saints
Game Ready). However, /promotions/ is Cloudflare-fronted to this scraper
and the base scratch-off listing carries no per-game flag. has_second_chance
stays FALSE for all LA games until a Cloudflare-passing fetch is wired.

Listing: https://louisianalottery.com/scratch-offs/
Each game card is <a href="https://louisianalottery.com/game/NUMBER-slug/" class="so-excerpt">
containing: <h3 class="so-excerpt__title">, <dl> with <div><dt>label<dd>value pairs:
  Ticket Price / Overall Odds / Top Prize / Game No.
Detail page (same URL): table with Tier Prize | Odds of Winning | Total | Claimed | Remaining.
"""
import re
import logging
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://louisianalottery.com/scratch-offs/"
BASE_URL = "https://louisianalottery.com"


class LouisianaScraper(BaseScraper):
    state_code = "LA"
    state_name = "Louisiana"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        soup = self.soup(GAMES_URL)
        games = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/game/" not in href:
                continue
            if href in seen:
                continue
            seen.add(href)

            h3 = a.find("h3")
            name = h3.get_text(strip=True) if h3 else ""
            if not name:
                continue

            # Price and odds — try dl/dt/dd first (old layout), fall back to regex on card text
            price = None
            overall_odds = None
            for div in a.select("dl div"):
                dt = div.find("dt")
                dd = div.find("dd")
                if not (dt and dd):
                    continue
                label = dt.get_text(strip=True).lower()
                value = dd.get_text(strip=True)
                if "ticket price" in label:
                    price = parse_prize_amount(value)
                elif "overall odds" in label:
                    overall_odds = parse_odds(value.replace(":", " in "))
            if not price:
                card_text = a.get_text(" ", strip=True)
                pm = re.search(r"Ticket\s+Price\s+\$?([\d.]+)", card_text, re.I)
                if pm:
                    price = float(pm.group(1))
                om = re.search(r"Overall\s+Odds\s+1[:\s]+([\d.]+)", card_text, re.I)
                if om:
                    overall_odds = parse_odds(om.group(1))

            if not price:
                continue

            m = re.search(r"/game/(\d+)-", href)
            game_id = m.group(1) if m else re.sub(r"[^a-z0-9]", "", name.lower())[:20]
            detail_url = href if href.startswith("http") else BASE_URL + href

            tiers = []
            try:
                tiers = self._scrape_detail(detail_url)
            except Exception as e:
                logger.debug("LA detail failed for %s: %s", name, e)

            img = a.find("img")
            image_url = img.get("src") if img else None

            all_have_rem = tiers and all(t.get("prizes_remaining") is not None for t in tiers)

            # Louisiana's overall_odds includes unlisted low-value prize tiers, so
            # overall_odds × listed_prizes underestimates total tickets. Use per-tier
            # original odds × prize counts instead (odds = total_tickets / prizes_in_tier).
            tier_totals = [round(t["prizes_total"] * t["odds_one_in"])
                           for t in tiers if t.get("prizes_total") and t.get("odds_one_in")]
            total_tickets = round(sum(tier_totals) / len(tier_totals)) if tier_totals else None

            tier_remaining = [round(t["prizes_remaining"] * t["odds_one_in"])
                              for t in tiers if t.get("prizes_remaining") is not None and t.get("odds_one_in")]
            tickets_remaining = round(sum(tier_remaining) / len(tier_remaining)) if (tier_remaining and all_have_rem) else None

            # When no prizes have been claimed yet, remaining == total — both values
            # are derived from the same original odds, so they carry no current signal.
            # Null them out so the game falls back to odds-only EV (which we also clear)
            # and the frontend shows the "Limited data" banner instead of fake ticket counts.
            no_claims = (
                all_have_rem
                and tiers
                and all(t.get("prizes_remaining") == t.get("prizes_total") for t in tiers)
            )

            # LA's overall_odds includes an unlisted free-ticket tier (win-the-price-back)
            # that isn't in the prize table. Synthesize it so EV reflects it. The implied
            # winner count: total_tickets / overall_odds minus the visible tier sum.
            synthesized = False
            if (
                tiers and total_tickets and overall_odds and overall_odds > 0
                and not no_claims
            ):
                visible_winners = sum((t.get("prizes_total") or 0) for t in tiers)
                expected_winners = total_tickets / overall_odds
                missing = round(expected_winners - visible_winners)
                if visible_winners > 0 and missing > visible_winners * 0.1:
                    if all_have_rem:
                        visible_remaining = sum(t["prizes_remaining"] for t in tiers)
                        missing_remaining = round(missing * visible_remaining / visible_winners)
                    else:
                        missing_remaining = None
                    tiers.append({
                        "prize_amount":     price,
                        "odds_one_in":      round(total_tickets / missing, 2),
                        "prizes_total":     missing,
                        "prizes_remaining": missing_remaining,
                    })
                    synthesized = True

            if no_claims:
                tickets_remaining = None
                total_tickets = None

            game = self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=tiers,
                overall_odds=overall_odds,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                detail_url=detail_url,
                image_url=image_url,
                ev_approximate=synthesized,
            )
            if no_claims:
                game["ev"] = None
                game["return_pct"] = None
            games.append(game)

        logger.info("LA: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str) -> list[dict]:
        """Fetch game page for Tier Prize | Odds of Winning | Total | Claimed | Remaining table."""
        soup = self.soup(url)
        for table in soup.find_all("table"):
            text = table.get_text().lower()
            if any(k in text for k in ("prize", "odds", "1 in")):
                tiers = self.parse_table_tiers(table)
                if tiers:
                    return tiers
        return []
