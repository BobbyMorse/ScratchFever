"""
Virginia Lottery scratch-off scraper.
Listing: https://www.valottery.com/Scratcher-Search (AngularJS SPA)
  API: POST /api/v1/scratchers — returns 18 games/page (5 pages total).
  Playwright loads the listing page then clicks "next" for all pages.
Detail:  https://www.valottery.com/scratchers/{GameID} (server-rendered)
  Table: Prize Amount | Winning Tickets At Start | Winning Tickets Unclaimed
  EV: overall_odds * sum(unclaimed) = tickets_remaining, then remaining-based EV.
"""
from __future__ import annotations
import re
import logging
from bs4 import BeautifulSoup
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

LIST_URL = "https://www.valottery.com/Scratcher-Search"
BASE_URL = "https://www.valottery.com"

# VA's Rewards/Promotions hub lists active second-chance promos. Each promo
# page names eligible scratchers and (typically) embeds the game number as
# "Game #NNNN". We crawl the promos index, fetch each promo page, and
# extract game numbers — no per-game flag on the listing API itself.
PROMOTIONS_INDEX_URL = "https://www.valottery.com/rewards/promotions"
SECOND_CHANCE_URL = "https://www.valottery.com/rewards/promotions"
_VA_GAME_NUM_RE = re.compile(r"Game\s*#\s*(\d{3,5})", re.I)


class VirginiaScraper(PlaywrightScraper):
    state_code = "VA"
    state_name = "Virginia"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        self._ensure_browser()
        page = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        ).new_page()

        all_games_raw = {}  # GameID -> {Title, TicketPrice, ...}

        def handle_response(resp):
            ct = resp.headers.get("content-type", "")
            if "json" in ct and "/api/v1/scratchers" in resp.url:
                try:
                    data = resp.json()
                    for g in data.get("data", []):
                        if g.get("GameID"):
                            all_games_raw[g["GameID"]] = g
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            page.goto(LIST_URL, wait_until="load", timeout=45_000)
            # Wait for Angular to render first page of games
            try:
                page.wait_for_selector(".scratcher-tile", timeout=20_000)
            except Exception:
                pass
            page.wait_for_timeout(2_000)
            # Paginate by triggering #pager-next click via JS (avoids visibility issues)
            for _ in range(10):
                disabled = page.evaluate(
                    "() => { "
                    "  const b = document.getElementById('pager-next'); "
                    "  if (!b) return 'missing'; "
                    "  if (b.disabled) return 'disabled'; "
                    "  b.click(); return 'clicked'; "
                    "}"
                )
                if disabled in ("missing", "disabled"):
                    break
                page.wait_for_timeout(2_500)
        except Exception as e:
            logger.warning("VA listing page error: %s", e)
        finally:
            try:
                page.close()
            except Exception:
                pass

        logger.info("VA: %d games from API", len(all_games_raw))

        sc_game_ids = self._fetch_second_chance_ids()
        logger.info("VA second-chance eligible game ids: %d", len(sc_game_ids))

        games = []
        for game_id, meta in all_games_raw.items():
            title = (meta.get("Title") or "").strip()
            price_str = meta.get("TicketPrice") or ""
            price = parse_prize_amount(price_str)
            if not price or not title:
                continue

            detail_url = f"{BASE_URL}/scratchers/{game_id}"
            tiers, overall_odds, image_url = self._scrape_detail(detail_url)

            tickets_remaining = None
            if overall_odds and tiers and all(t.get("prizes_remaining") is not None for t in tiers):
                total_rem = sum(t["prizes_remaining"] or 0 for t in tiers)
                if total_rem > 0:
                    tickets_remaining = round(overall_odds * total_rem)

            total_tickets = None
            if overall_odds and tiers and all(t.get("prizes_total") is not None for t in tiers):
                total_all = sum(t["prizes_total"] or 0 for t in tiers)
                if total_all > 0:
                    total_tickets = round(overall_odds * total_all)

            has_2c = str(game_id) in sc_game_ids
            games.append(self.build_game(
                game_id=str(game_id),
                name=title,
                price=price,
                tiers=tiers,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                overall_odds=overall_odds,
                detail_url=detail_url,
                image_url=image_url,
                has_second_chance=has_2c,
                second_chance_url=SECOND_CHANCE_URL if has_2c else None,
            ))

        logger.info("VA: %d games scraped", len(games))
        return games

    def _fetch_second_chance_ids(self) -> set[str]:
        """Crawl /rewards/promotions for active second-chance promos and
        extract eligible scratcher Game #s. Each promo card links to a
        detail page whose copy reads "Game #NNNN" for every eligible
        scratcher. Empty set on failure — never blanket-flag."""
        ids: set[str] = set()
        try:
            resp = self.get(PROMOTIONS_INDEX_URL, timeout=20)
        except Exception as e:
            logger.warning("VA promotions index fetch failed: %s", e)
            return ids
        # Index page links each promo at /rewards/promotions/<slug>.
        slug_re = re.compile(r'href="/rewards/promotions/([a-z0-9\-]+)"', re.I)
        slugs = set(slug_re.findall(resp.text))
        for slug in slugs:
            try:
                page_resp = self.get(f"{BASE_URL}/rewards/promotions/{slug}", timeout=15)
            except Exception as e:
                logger.debug("VA promo %s fetch failed: %s", slug, e)
                continue
            for m in _VA_GAME_NUM_RE.finditer(page_resp.text):
                ids.add(m.group(1))
        return ids

    def _scrape_detail(self, url: str) -> tuple[list, float | None, str | None]:
        soup = self.soup(url)
        text = soup.get_text(" ", strip=True)

        overall_odds = None
        om = re.search(r"1\s+in\s+([\d.]+)", text, re.I)
        if om:
            overall_odds = float(om.group(1))

        # Ticket image — VA serves it from one of two media folders under
        # several naming conventions: "{id}_unscratched", "{id}-scratched",
        # "{id}-unscratched", or just the bare game-number filename.
        image_url = None
        for img in soup.find_all("img", src=True):
            src = img["src"]
            low = src.lower()
            if any(tag in low for tag in
                   ("_unscratched", "-unscratched", "-scratched", "_scratched")):
                image_url = src.split("?")[0]
                break

        tiers = []
        for table in soup.find_all("table"):
            hdrs = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not any("prize" in h for h in hdrs):
                continue
            prize_col = next((i for i, h in enumerate(hdrs) if "prize" in h and "amount" in h or h == "prize amount"), 0)
            total_col = next((i for i, h in enumerate(hdrs) if "start" in h or "total" in h), 1)
            rem_col = next((i for i, h in enumerate(hdrs) if "unclaimed" in h or "remaining" in h), 2)
            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= max(prize_col, rem_col):
                    continue
                prize = parse_prize_amount(cells[prize_col].get_text(strip=True).replace("*", ""))
                try:
                    remaining = int(cells[rem_col].get_text(strip=True).replace(",", ""))
                except (ValueError, IndexError):
                    remaining = None
                try:
                    total = int(cells[total_col].get_text(strip=True).replace(",", ""))
                except (ValueError, IndexError):
                    total = None
                if prize and prize > 0:
                    tiers.append({
                        "prize_amount": prize,
                        "prizes_remaining": remaining,
                        "prizes_total": total,
                        "odds_one_in": None,
                    })
            if tiers:
                break

        return tiers, overall_odds, image_url
