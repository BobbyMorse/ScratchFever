"""
Massachusetts State Lottery scratch-off scraper.

Two APIs:
  GET /api/v1/games              → active scratch games with official overall odds
  GET /api/v1/instant-game-prizes → prize tier data (totalPrizes, prizesRemaining)

Formula
-------
  total_tickets     = official_odds × total_prizes_printed
  tickets_remaining = official_odds × total_prizes_remaining
  EV                = Σ(prize_i × prizes_remaining_i) / tickets_remaining − price
"""
from __future__ import annotations
import logging
import time
from bs4 import BeautifulSoup
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_odds, calculate_jackpot_odds, calculate_ev

logger = logging.getLogger(__name__)

BASE_API = "https://mslc-prod-herokuapp-com.global.ssl.fastly.net"
PRIZES_URL = f"{BASE_API}/api/v1/instant-game-prizes"
GAMES_URL  = f"{BASE_API}/api/v1/games"
DETAIL_BASE = "https://www.masslottery.com/games/draw-and-instants"

_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.masslottery.com",
    "Referer": "https://www.masslottery.com/",
}


class MassachusettsScraper(BaseScraper):
    state_code = "MA"
    state_name = "Massachusetts"
    base_url = "https://www.masslottery.com"

    def scrape(self) -> list[dict]:
        active_games = self._fetch_active_games()
        logger.info("MA active scratch games: %d", len(active_games))

        resp = self.get(PRIZES_URL, headers=_HEADERS)
        items = resp.json()
        logger.info("MA prize API returned %d games", len(items))

        games = []
        skipped = 0
        for item in items:
            slug = item.get("gameIdentifier", "")
            if active_games and slug not in active_games:
                skipped += 1
                continue
            try:
                meta = active_games.get(slug, {})
                official_odds = meta.get("odds")
                image_url = meta.get("image_url")
                start_date = meta.get("start_date")
                game = self._parse_item(item, official_odds, image_url, start_date)
                if game:
                    game["how_to_play"] = self._fetch_how_to_play(slug)
                    time.sleep(0.3)  # polite rate limit
                    games.append(game)
            except Exception as e:
                logger.debug("MA parse error for %s: %s", item.get("gameName"), e)

        logger.info("MA: %d active games parsed, %d inactive skipped", len(games), skipped)
        return games

    def _fetch_how_to_play(self, slug: str) -> str | None:
        """Scrape the 'How to Play' section from the MA Lottery game detail page."""
        try:
            url = f"{DETAIL_BASE}/{slug}"
            resp = self.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, "lxml")

            # Find any heading that says "How to Play" and collect text that follows
            for tag in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
                if "how to play" in tag.get_text(strip=True).lower():
                    parts = []
                    for sib in tag.find_next_siblings():
                        if sib.name in ["h2", "h3", "h4"]:
                            break
                        text = sib.get_text(separator=" ", strip=True)
                        if text:
                            parts.append(text)
                    if parts:
                        return " ".join(parts)[:3000]

            # Fallback: look for a div/section with class containing "how" or "play"
            for el in soup.find_all(["div", "section"], class_=True):
                classes = " ".join(el.get("class", [])).lower()
                if "how" in classes or "play" in classes or "instructions" in classes:
                    text = el.get_text(separator=" ", strip=True)
                    if len(text) > 50:
                        return text[:3000]

            return None
        except Exception as e:
            logger.debug("how_to_play fetch failed for %s: %s", slug, e)
            return None

    def _fetch_active_games(self) -> dict:
        """Returns {identifier: {odds, image_url, start_date}} for active scratch games."""
        try:
            resp = self.get(GAMES_URL, headers=_HEADERS)
            result = {}
            for g in resp.json():
                if g.get("gameType") != "Scratch":
                    continue
                if "Expiring Game" in g.get("tags", []):
                    continue
                slug = g.get("identifier", "")
                odds = parse_odds(g.get("odds", ""))
                icon_url = g.get("icon", {}).get("url", "") or ""
                if icon_url.startswith("//"):
                    icon_url = "https:" + icon_url
                # MA API field names vary; try common ones for launch date.
                start_date = None
                for k in ("startDate", "releaseDate", "launchDate", "saleStartDate", "created", "publishedAt"):
                    v = g.get(k)
                    if v:
                        start_date = str(v)[:10]
                        break
                result[slug] = {
                    "odds": odds,
                    "image_url": icon_url or None,
                    "start_date": start_date,
                }
            return result
        except Exception as e:
            logger.warning("MA could not fetch active games list: %s — including all", e)
            return {}

    def _parse_item(self, item: dict, official_odds: float | None, image_url: str | None = None,
                    start_date: str | None = None) -> dict | None:
        game_id   = str(item.get("massGameID", ""))
        name      = item.get("gameName", "")
        slug      = item.get("gameIdentifier", "")
        price     = float(item.get("ticketCost") or 0)
        tiers_raw = item.get("prizeTiers") or []
        # Fall back to start_date on the prize item if the active-games list didn't carry one.
        if not start_date:
            for k in ("startDate", "releaseDate", "launchDate", "saleStartDate"):
                v = item.get(k)
                if v:
                    start_date = str(v)[:10]
                    break

        if not name or not price or not tiers_raw:
            return None

        tiers = []
        total_prizes_printed   = 0
        total_prizes_remaining = 0

        for t in tiers_raw:
            prize     = float(t.get("prizeAmount") or 0)
            total     = int(t.get("totalPrizes") or 0)
            remaining = int(t.get("prizesRemaining") or 0)

            if prize <= 0 or total <= 0:
                continue

            total_prizes_printed   += total
            total_prizes_remaining += remaining

            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      None,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })

        if not tiers or not official_odds or official_odds <= 0:
            return None

        total_tickets = round(official_odds * total_prizes_printed)
        # Only store tickets_remaining when the API provides real remaining counts.
        # prizesRemaining=0 for all tiers means data is unavailable, not genuinely sold out.
        tickets_remaining = round(official_odds * total_prizes_remaining) if total_prizes_remaining > 0 else None

        for t in tiers:
            t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2) if t["prizes_total"] else None

        # Apply name-based annuity heuristic so the top tier can carry cash_value / annuity
        # metadata before EV math runs. Honors any scraper-supplied annuity fields.
        _apply_annuity_heuristic(name, tiers)

        if tickets_remaining and tickets_remaining > 0:
            ev_data = calculate_ev(price, tiers, tickets_remaining)
            ev = ev_data["ev"]
            return_pct = ev_data["return_pct"]
        else:
            ev = None
            return_pct = None

        top_tier = max(tiers, key=lambda t: t["prize_amount"])
        jackpot_odds = calculate_jackpot_odds(tiers, tickets_remaining)

        return {
            "game_id":              game_id or slug,
            "name":                 name,
            "price":                price,
            "ev":                   ev,
            "return_pct":           return_pct,
            "overall_odds_one_in":  official_odds,
            "top_prize":            top_tier["prize_amount"],
            "top_prize_remaining":  top_tier["prizes_remaining"],
            "jackpot_odds_one_in":  jackpot_odds,
            "total_tickets":        total_tickets,
            "tickets_remaining":    tickets_remaining,
            "detail_url":           f"{DETAIL_BASE}/{slug}",
            "image_url":            image_url,
            "start_date":           start_date,
            "top_prize_is_annuity": bool(top_tier.get("is_annuity")),
            "top_prize_cash_value": top_tier.get("cash_value"),
            "top_prize_annuity_years": top_tier.get("annuity_years"),
            "top_prize_annuity_annual": top_tier.get("annuity_annual"),
            # MA runs second-chance via My VIP Club for many scratch games.
            "has_second_chance":    True,
            "second_chance_url":    "https://www.masslottery.com/win-stations/my-vip-club",
            "tiers":                tiers,
        }
