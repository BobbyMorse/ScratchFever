"""
Minnesota Lottery scratch-off scraper.
Listing: https://www.mnlottery.com/games/scratch/ (static HTML)
Detail:  https://www.mnlottery.com/games/scratch/{slug} (JavaScript-rendered)
  Static table:  "To Win | Odds* | Number of Prizes**"
  JS-rendered:   "Prize | Claimed | Remaining"  (loaded after page init)

Uses Playwright with network-response interception. If no JSON prize API is
found, falls back to parsing the JS-rendered DOM for the Claimed/Remaining
table. Intercepts any response whose URL contains "prize" or "remaining" and
content-type is JSON — MN's CMS may surface this as a Drupal JSON:API call.
"""
from __future__ import annotations
import re
import logging
from bs4 import BeautifulSoup
from backend.scraper.playwright_base import PlaywrightScraper, _UA
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

GAMES_URL = "https://www.mnlottery.com/games/scratch/"
BASE_URL = "https://www.mnlottery.com"


class MinnesotaScraper(PlaywrightScraper):
    state_code = "MN"
    state_name = "Minnesota"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        # ── Step 1: game listing via static HTML ──────────────────────────────
        soup = self.soup(GAMES_URL)
        game_stubs = []  # (slug, name, price, detail_url, image_url)
        seen: set[str] = set()

        slug_to_anchor: dict[str, object] = {}
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not re.search(r"/games/scratch/[a-z0-9-]+", href):
                continue
            slug = href.rstrip("/").split("/")[-1]
            if slug and re.search(r"[a-z]", slug) and slug not in slug_to_anchor:
                slug_to_anchor[slug] = a

        for slug, a in slug_to_anchor.items():
            if slug in seen:
                continue
            try:
                card = a
                for _ in range(6):
                    slugs_in_card = {
                        x.get("href", "").rstrip("/").split("/")[-1]
                        for x in card.find_all("a", href=True)
                        if re.search(r"/games/scratch/[a-z0-9-]+", x.get("href", ""))
                    }
                    if slugs_in_card == {slug} and card.find(["h2", "h3", "h4", "h5", "h6"]):
                        break
                    if card.parent:
                        card = card.parent

                name_el = card.find(["h2", "h3", "h4", "h5", "h6"])
                name = name_el.get_text(strip=True) if name_el else ""
                if not name:
                    continue

                # Strip the heading before searching for price — names like
                # "Red Hot $1,000's" otherwise hijack the price regex and
                # produce $1 instead of $10 (→ 10× inflated return %).
                card_text = card.get_text(" ", strip=True)
                price_text = card_text.replace(name, " ", 1) if name in card_text else card_text
                # Negative lookahead rejects digits/commas right after the number
                # so a stray "$1,000's" left in price_text still won't match.
                pm = re.search(r"\$\s*(\d+(?:\.\d+)?)(?![\d,])", price_text)
                price = float(pm.group(1)) if pm else None
                if not price:
                    continue

                img_el = card.find("img")
                image_url = img_el.get("src") if img_el else None

                seen.add(slug)
                href = a.get("href", "")
                detail_url = (BASE_URL + href) if href.startswith("/") else href
                game_stubs.append((slug, name, price, detail_url, image_url))
            except Exception as e:
                logger.debug("MN listing parse error: %s", e)

        logger.info("MN: %d game stubs from listing", len(game_stubs))

        # ── Step 2: Playwright detail pages for Claimed/Remaining ─────────────
        self._ensure_browser()
        games = []
        for slug, name, price, detail_url, image_url in game_stubs:
            try:
                tiers, overall_odds, tickets_remaining, total_tickets = \
                    self._scrape_detail_pw(detail_url)
                games.append(self.build_game(
                    game_id=slug,
                    name=name,
                    price=price,
                    tiers=tiers,
                    overall_odds=overall_odds,
                    tickets_remaining=tickets_remaining,
                    total_tickets=total_tickets,
                    detail_url=detail_url,
                    image_url=image_url,
                ))
            except Exception as e:
                logger.debug("MN detail failed for %s: %s", name, e)

        logger.info("MN: %d games built", len(games))
        return games

    def _scrape_detail_pw(self, url: str) -> tuple[list, float | None, int | None, int | None]:
        captured_json: list[dict] = []

        def _on_response(resp):
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            url_lower = resp.url.lower()
            if any(kw in url_lower for kw in ("prize", "remaining", "claim", "scratch")):
                try:
                    captured_json.append(resp.json())
                except Exception:
                    pass

        ctx = self._browser.new_context(user_agent=_UA)
        page = ctx.new_page()
        page.on("response", _on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=30_000)
            # Wait an extra beat for any deferred prize data XHR
            page.wait_for_timeout(2_000)
            html = page.content()
        finally:
            page.close()
            ctx.close()

        soup = BeautifulSoup(html, "lxml")

        # ── Try JSON API data first ────────────────────────────────────────────
        tiers = self._parse_json_prizes(captured_json)

        # ── Fall back to DOM table parsing ────────────────────────────────────
        if not tiers:
            tiers = self._parse_dom_prizes(soup)

        # ── Overall odds from page text ───────────────────────────────────────
        page_text = soup.get_text(" ", strip=True)
        overall_odds: float | None = None
        om = re.search(r"1\s+in\s+([\d,.]+)", page_text, re.I)
        if om:
            overall_odds = float(om.group(1).replace(",", ""))

        # ── Derive ticket counts ──────────────────────────────────────────────
        tickets_remaining: int | None = None
        total_tickets: int | None = None

        has_remaining = any(t.get("prizes_remaining") is not None for t in tiers)
        has_total = any(t.get("prizes_total") is not None for t in tiers)

        if overall_odds and overall_odds > 0:
            if has_remaining:
                total_rem = sum(t["prizes_remaining"] or 0 for t in tiers)
                if total_rem > 0:
                    tickets_remaining = round(overall_odds * total_rem)
            if has_total:
                total_all = sum(t["prizes_total"] or 0 for t in tiers)
                if total_all > 0:
                    total_tickets = round(overall_odds * total_all)
        else:
            # No overall odds — try regex fallbacks from page text
            m_total = re.search(r"(\d[\d,]+)\s*total\s*tickets?", page_text, re.I)
            if m_total:
                total_tickets = int(m_total.group(1).replace(",", ""))
            m_rem = re.search(r"(\d[\d,]+)\s*tickets?\s*(?:still\s*)?(?:remaining|available)", page_text, re.I)
            if m_rem:
                tickets_remaining = int(m_rem.group(1).replace(",", ""))

        return tiers, overall_odds, tickets_remaining, total_tickets

    # ── JSON API parser ────────────────────────────────────────────────────────

    def _parse_json_prizes(self, blobs: list) -> list[dict]:
        """Try to extract prize tiers from intercepted JSON API responses."""
        for blob in blobs:
            tiers = self._extract_tiers_from_blob(blob)
            if tiers:
                return tiers
        return []

    def _extract_tiers_from_blob(self, obj, depth: int = 0) -> list[dict]:
        if depth > 6:
            return []
        if isinstance(obj, list):
            for item in obj:
                result = self._extract_tiers_from_blob(item, depth + 1)
                if result:
                    return result
        if isinstance(obj, dict):
            keys = {k.lower() for k in obj}
            # Heuristic: a prize tier object has "prize" + ("claimed"/"remaining"/"total")
            if any("prize" in k for k in keys) and (
                any("claim" in k for k in keys)
                or any("remain" in k for k in keys)
                or any("total" in k for k in keys)
            ):
                # Try to build a single tier
                prize_val = self._find_key(obj, ("prize", "amount", "prize_amount", "prizeAmount"))
                claimed_val = self._find_key(obj, ("claimed", "claimedCount", "prizes_claimed"))
                remaining_val = self._find_key(obj, ("remaining", "remainingCount", "prizes_remaining"))
                total_val = self._find_key(obj, ("total", "starting", "prizes_total", "totalCount"))
                if prize_val is not None:
                    prize = parse_prize_amount(str(prize_val))
                    if prize and prize > 0:
                        try:
                            remaining = int(remaining_val) if remaining_val is not None else None
                        except (TypeError, ValueError):
                            remaining = None
                        try:
                            total = int(total_val) if total_val is not None else None
                        except (TypeError, ValueError):
                            total = None
                        return [{
                            "prize_amount": prize,
                            "prizes_remaining": remaining,
                            "prizes_total": total,
                            "odds_one_in": None,
                        }]
            # Recurse into values
            for v in obj.values():
                result = self._extract_tiers_from_blob(v, depth + 1)
                if result:
                    return result
        return []

    @staticmethod
    def _find_key(obj: dict, candidates: tuple) -> object:
        for c in candidates:
            for k, v in obj.items():
                if k.lower() == c.lower():
                    return v
        return None

    # ── DOM table parser ───────────────────────────────────────────────────────

    def _parse_dom_prizes(self, soup: BeautifulSoup) -> list[dict]:
        # Remove MN accessibility spans that repeat column headers inside cells
        for span in soup.find_all("span", class_="bulletin-matrix-table__label"):
            span.decompose()

        tiers = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_cells = rows[0].find_all(["th", "td"])
            headers = [c.get_text(strip=True).lower() for c in header_cells]

            has_claimed = any("claim" in h for h in headers)
            has_remaining = any("remain" in h for h in headers)
            has_prize_col = any("prize" in h or "win" in h for h in headers)

            if not has_prize_col:
                continue

            # Prefer Claimed/Remaining table; fall back to Number of Prizes table
            if has_claimed or has_remaining:
                prize_col = next((i for i, h in enumerate(headers) if "prize" in h or "win" in h), 0)
                claim_col = next((i for i, h in enumerate(headers) if "claim" in h), None)
                remain_col = next((i for i, h in enumerate(headers) if "remain" in h), None)
                total_col = next((i for i, h in enumerate(headers) if "total" in h or "start" in h or "number" in h), None)

                for row in rows[1:]:
                    cells = row.find_all(["td", "th"])
                    if len(cells) <= prize_col:
                        continue
                    prize = parse_prize_amount(cells[prize_col].get_text(strip=True))
                    if not prize or prize <= 0:
                        continue

                    def _int(col):
                        if col is None or len(cells) <= col:
                            return None
                        try:
                            return int(cells[col].get_text(strip=True).replace(",", ""))
                        except (ValueError, TypeError):
                            return None

                    claimed = _int(claim_col)
                    remaining = _int(remain_col)
                    total = _int(total_col)
                    # Derive total from claimed + remaining if not in a dedicated column
                    if total is None and claimed is not None and remaining is not None:
                        total = claimed + remaining

                    tiers.append({
                        "prize_amount": prize,
                        "prizes_remaining": remaining,
                        "prizes_total": total,
                        "odds_one_in": None,
                    })
                if tiers:
                    return tiers

            else:
                # Static "To Win | Odds | Number of Prizes" table
                has_odds = any("odd" in h for h in headers)
                if not has_odds:
                    continue
                prize_col = next((i for i, h in enumerate(headers) if ("win" in h and "number" not in h) or "prize" in h), 0)
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
                    if prize and prize > 0:
                        tiers.append({
                            "prize_amount": prize,
                            "odds_one_in": odds,
                            "prizes_total": total,
                            "prizes_remaining": None,
                        })
                if tiers:
                    return tiers

        return tiers
