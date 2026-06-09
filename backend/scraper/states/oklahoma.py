"""
Oklahoma Lottery scratch-off scraper.

lottery.ok.gov/scratchers is a JavaScript SPA (Vue/React + Axios).
Strategy:
  1. Load the listing page with Playwright; extract game IDs from rendered <a> hrefs.
  2. For each game detail page (/scratchers/{id}), intercept ALL JSON responses
     before navigating so we capture whichever API call backs the ODDS and
     PRIZES REMAINING tabs without needing to know the exact endpoint URL.
  3. If no JSON is captured for a tab, click the tab button and wait, then parse DOM.
  4. EV: tickets_remaining = overall_odds × sum(prizes_remaining),
         total_tickets     = overall_odds × sum(prizes_total)
"""
from __future__ import annotations

import re
import logging
from bs4 import BeautifulSoup
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lottery.ok.gov"
LIST_URL = f"{BASE_URL}/scratchers"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class OklahomaScraper(PlaywrightScraper):
    state_code = "OK"
    state_name = "Oklahoma"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        self._ensure_browser()
        ctx = self._browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})

        try:
            game_ids = self._get_game_ids(ctx)
            logger.info("OK: found %d game IDs", len(game_ids))
            if not game_ids:
                return []

            games = []
            for gid in game_ids:
                result = self._scrape_detail(ctx, gid)
                if result:
                    games.append(result)

            logger.info("OK: %d games scraped", len(games))
            return games
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    # ── listing page ──────────────────────────────────────────────────────────

    def _get_game_ids(self, ctx) -> list[str]:
        page = ctx.new_page()
        game_ids: list[str] = []
        captured_json: dict[str, object] = {}

        def handle_resp(resp):
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                try:
                    captured_json[resp.url] = resp.json()
                except Exception:
                    pass

        page.on("response", handle_resp)
        try:
            page.goto(LIST_URL, wait_until="networkidle", timeout=45_000)
            page.wait_for_timeout(3_000)

            # First try: extract from rendered <a href="/scratchers/DIGITS"> links
            html = page.content()
            for href in re.findall(r"""href=["']/scratchers/(\d+)["']""", html):
                if href not in game_ids:
                    game_ids.append(href)

            # Second try: extract from intercepted JSON
            if not game_ids:
                for data in captured_json.values():
                    ids = _extract_ids_from_json(data)
                    for i in ids:
                        if i not in game_ids:
                            game_ids.append(i)

            # Third try: if still empty, look for any /scratchers/\d+ in page text
            if not game_ids:
                soup = BeautifulSoup(html, "lxml")
                for a in soup.find_all("a", href=True):
                    m = re.match(r"^/scratchers/(\d+)$", a["href"])
                    if m:
                        gid = m.group(1)
                        if gid not in game_ids:
                            game_ids.append(gid)
        except Exception as e:
            logger.warning("OK: listing page error: %s", e)
        finally:
            try:
                page.close()
            except Exception:
                pass

        return game_ids

    # ── detail page ───────────────────────────────────────────────────────────

    def _scrape_detail(self, ctx, game_id: str) -> dict | None:
        url = f"{BASE_URL}/scratchers/{game_id}"
        page = ctx.new_page()

        # All intercepted JSON keyed by URL
        captured: dict[str, object] = {}

        def handle_resp(resp):
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                try:
                    captured[resp.url] = resp.json()
                except Exception:
                    pass

        page.on("response", handle_resp)
        try:
            page.goto(url, wait_until="networkidle", timeout=45_000)
            page.wait_for_timeout(2_000)

            # Try to trigger ODDS tab (click by text)
            _click_tab(page, ["ODDS", "Odds", "odds"])
            page.wait_for_timeout(1_500)

            # Try to trigger PRIZES REMAINING tab
            _click_tab(page, ["PRIZES REMAINING", "Prizes Remaining", "remaining"])
            page.wait_for_timeout(1_500)

            html = page.content()
        except Exception as e:
            logger.warning("OK: detail page error for %s: %s", game_id, e)
            try:
                html = page.content()
            except Exception:
                return None
        finally:
            try:
                page.close()
            except Exception:
                pass

        # Attempt to parse game data from intercepted JSON
        meta = _find_game_meta(captured, game_id)
        odds_tiers = _find_odds_tiers(captured)
        remaining_tiers = _find_remaining_tiers(captured)

        # Fall back to DOM parsing if JSON didn't give us what we need
        soup = BeautifulSoup(html, "lxml")
        if not meta:
            meta = _parse_meta_from_dom(soup, game_id)
        if not odds_tiers:
            odds_tiers = _parse_odds_table(soup)
        if not remaining_tiers:
            remaining_tiers = _parse_remaining_table(soup)

        name = (meta or {}).get("name", "")
        price = (meta or {}).get("price")
        overall_odds = (meta or {}).get("overall_odds")
        image_url = (meta or {}).get("image_url", "")

        if not name or not price:
            logger.debug("OK: skipping %s — missing name or price (meta=%s)", game_id, meta)
            return None

        # Merge tiers: start with odds_tiers, fill in remaining from remaining_tiers
        tiers = _merge_tiers(odds_tiers, remaining_tiers)

        if not tiers:
            logger.debug("OK: no tiers for game %s", game_id)
            return None

        # Estimate total_tickets and tickets_remaining
        total_tickets = None
        tickets_remaining = None

        if overall_odds and tiers:
            total_prizes = sum(t.get("prizes_total") or 0 for t in tiers)
            rem_prizes = sum(t.get("prizes_remaining") or 0 for t in tiers)
            if total_prizes > 0:
                total_tickets = round(overall_odds * total_prizes)
            if rem_prizes > 0 and overall_odds:
                tickets_remaining = round(overall_odds * rem_prizes)

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            tickets_remaining=tickets_remaining,
            total_tickets=total_tickets,
            overall_odds=overall_odds,
            detail_url=url,
            image_url=image_url,
        )


# ── JSON extraction helpers ────────────────────────────────────────────────────

def _extract_ids_from_json(data, depth: int = 0) -> list[str]:
    """Recursively search JSON for game IDs."""
    if depth > 4:
        return []
    ids = []
    id_keys = {"id", "gameId", "GameId", "game_id", "GameID", "scratcherId", "ScratcherID"}
    if isinstance(data, list):
        for item in data:
            ids.extend(_extract_ids_from_json(item, depth + 1))
    elif isinstance(data, dict):
        for k in id_keys:
            if k in data:
                v = str(data[k])
                if v.isdigit():
                    ids.append(v)
        for v in data.values():
            if isinstance(v, (list, dict)):
                ids.extend(_extract_ids_from_json(v, depth + 1))
    return ids


def _find_game_meta(captured: dict, game_id: str) -> dict | None:
    """Find name/price/overall_odds in captured JSON."""
    name_keys = {"name", "Name", "title", "Title", "gameName", "GameName", "description"}
    price_keys = {"price", "Price", "ticketPrice", "TicketPrice", "cost", "Cost"}
    odds_keys = {"overallOdds", "overall_odds", "overallOddsOneIn", "odds"}

    for data in captured.values():
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            name = None
            for k in name_keys:
                if k in item and item[k]:
                    name = str(item[k]).strip()
                    break
            price = None
            for k in price_keys:
                if k in item:
                    price = parse_prize_amount(str(item[k]))
                    break
            if name and price:
                overall_odds = None
                for k in odds_keys:
                    if k in item:
                        try:
                            overall_odds = float(item[k])
                        except (ValueError, TypeError):
                            pass
                        break
                image_url = (
                    item.get("imageUrl") or item.get("ImageUrl") or
                    item.get("image_url") or item.get("image") or ""
                )
                return {"name": name, "price": price, "overall_odds": overall_odds, "image_url": image_url}
    return None


def _find_odds_tiers(captured: dict) -> list[dict]:
    """Find per-tier odds rows in captured JSON."""
    for url, data in captured.items():
        if any(kw in url.lower() for kw in ["odds", "chance", "winning", "prize"]):
            rows = _json_to_tier_rows(data)
            if rows:
                return rows
    # Broader scan
    for data in captured.values():
        rows = _json_to_tier_rows(data)
        if rows:
            return rows
    return []


def _find_remaining_tiers(captured: dict) -> list[dict]:
    """Find prizes-remaining rows in captured JSON."""
    for url, data in captured.items():
        if any(kw in url.lower() for kw in ["remaining", "unclaim", "available"]):
            rows = _json_to_tier_rows(data)
            if rows:
                return rows
    return []


def _json_to_tier_rows(data) -> list[dict]:
    """Try to interpret JSON as a list of prize tiers."""
    items = data if isinstance(data, list) else []
    if isinstance(data, dict):
        for k in ["prizes", "tiers", "data", "items", "results", "oddsTiers", "prizeBreakdown"]:
            if k in data and isinstance(data[k], list):
                items = data[k]
                break
    if not items:
        return []
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        prize = None
        for k in ["prize", "Prize", "prizeAmount", "PrizeAmount", "amount", "Amount", "value", "Value"]:
            if k in item:
                prize = parse_prize_amount(str(item[k]))
                break
        if not prize or prize <= 0:
            continue
        odds_in = None
        for k in ["odds", "Odds", "oddsOneIn", "OddsOneIn", "winningOdds", "oddsOf"]:
            if k in item:
                try:
                    v = str(item[k]).replace(",", "")
                    m = re.search(r"[\d.]+", v)
                    if m:
                        odds_in = float(m.group())
                except Exception:
                    pass
                break
        total = None
        for k in ["total", "Total", "prizesTotal", "PrizesTotal", "numberOfPrizes", "totalPrizes",
                   "winningTickets", "WinningTickets"]:
            if k in item:
                try:
                    total = int(str(item[k]).replace(",", ""))
                except (ValueError, TypeError):
                    pass
                break
        remaining = None
        for k in ["remaining", "Remaining", "prizesRemaining", "PrizesRemaining",
                   "unclaimedPrizes", "UnclaimedPrizes", "available", "Available",
                   "unclaimed", "Unclaimed"]:
            if k in item:
                try:
                    remaining = int(str(item[k]).replace(",", ""))
                except (ValueError, TypeError):
                    pass
                break
        rows.append({
            "prize_amount": prize,
            "odds_one_in": odds_in,
            "prizes_total": total,
            "prizes_remaining": remaining,
        })
    return rows if any(r.get("prizes_total") or r.get("odds_one_in") for r in rows) else []


# ── DOM parsing helpers ────────────────────────────────────────────────────────

def _parse_meta_from_dom(soup: BeautifulSoup, game_id: str) -> dict | None:
    text = soup.get_text(" ", strip=True)

    # Game name: try <h1>, <h2>, title tag
    name = None
    for tag in soup.find_all(["h1", "h2"]):
        t = tag.get_text(strip=True)
        if t and len(t) > 3 and "lottery" not in t.lower():
            name = t
            break
    if not name:
        title = soup.find("title")
        if title:
            name = title.get_text(strip=True).split("|")[0].strip()

    # Price: look for "$X" near "ticket" or at page start
    price = None
    m = re.search(r"\$(\d+)\s*(?:ticket|scratch)", text, re.I)
    if not m:
        m = re.search(r"ticket\s+price[:\s]+\$?([\d.]+)", text, re.I)
    if m:
        price = float(m.group(1))

    # Overall odds
    overall_odds = None
    om = re.search(r"overall.*?1\s+in\s+([\d.]+)", text, re.I)
    if not om:
        om = re.search(r"1\s+in\s+([\d.]+)", text, re.I)
    if om:
        overall_odds = float(om.group(1))

    # Image
    img = soup.find("img", src=re.compile(r"scratch|ticket|game", re.I))
    image_url = img["src"] if img else ""
    if image_url and image_url.startswith("/"):
        image_url = f"{BASE_URL}{image_url}"

    if not name or not price:
        return None
    return {"name": name, "price": price, "overall_odds": overall_odds, "image_url": image_url}


def _parse_odds_table(soup: BeautifulSoup) -> list[dict]:
    """Parse the ODDS tab table: Prize | Odds (1 in X)."""
    for table in soup.find_all("table"):
        hdrs = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not any("prize" in h or "award" in h for h in hdrs):
            continue
        odds_col = next((i for i, h in enumerate(hdrs) if "odd" in h or "chance" in h), None)
        prize_col = next((i for i, h in enumerate(hdrs) if "prize" in h or "award" in h), 0)
        rows = []
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            prize = parse_prize_amount(cells[prize_col]) if len(cells) > prize_col else None
            if not prize or prize <= 0:
                continue
            odds_in = None
            if odds_col is not None and len(cells) > odds_col:
                raw = cells[odds_col].replace(",", "")
                m = re.search(r"[\d.]+", raw)
                if m:
                    odds_in = float(m.group())
            rows.append({"prize_amount": prize, "odds_one_in": odds_in, "prizes_total": None, "prizes_remaining": None})
        if rows:
            return rows
    return []


def _parse_remaining_table(soup: BeautifulSoup) -> list[dict]:
    """Parse the PRIZES REMAINING tab table: Prize | Remaining | Total."""
    for table in soup.find_all("table"):
        hdrs = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not any("prize" in h for h in hdrs):
            continue
        if not any("remain" in h or "total" in h for h in hdrs):
            continue
        prize_col = next((i for i, h in enumerate(hdrs) if "prize" in h), 0)
        rem_col = next((i for i, h in enumerate(hdrs) if "remain" in h), None)
        total_col = next((i for i, h in enumerate(hdrs) if "total" in h), None)
        rows = []
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            prize = parse_prize_amount(cells[prize_col]) if len(cells) > prize_col else None
            if not prize or prize <= 0:
                continue
            remaining, total = None, None
            if rem_col is not None and len(cells) > rem_col:
                try:
                    remaining = int(cells[rem_col].replace(",", ""))
                except ValueError:
                    pass
            if total_col is not None and len(cells) > total_col:
                try:
                    total = int(cells[total_col].replace(",", ""))
                except ValueError:
                    pass
            rows.append({"prize_amount": prize, "odds_one_in": None, "prizes_total": total, "prizes_remaining": remaining})
        if rows:
            return rows
    return []


def _merge_tiers(odds_tiers: list[dict], remaining_tiers: list[dict]) -> list[dict]:
    """Merge odds and remaining data by prize_amount."""
    if not odds_tiers and not remaining_tiers:
        return []

    # Build lookup from remaining tiers by prize amount
    rem_by_prize: dict[float, dict] = {t["prize_amount"]: t for t in remaining_tiers}

    if odds_tiers:
        merged = []
        for t in odds_tiers:
            tier = dict(t)
            rem = rem_by_prize.get(t["prize_amount"])
            if rem:
                tier["prizes_remaining"] = rem.get("prizes_remaining")
                tier["prizes_total"] = rem.get("prizes_total") or tier.get("prizes_total")
            merged.append(tier)
        return merged

    # Only have remaining tiers
    return remaining_tiers


def _click_tab(page, texts: list[str]) -> bool:
    """Try to click a tab button matching one of the given text strings."""
    for text in texts:
        try:
            btn = page.locator(f"button:has-text('{text}'), a:has-text('{text}'), li:has-text('{text}')")
            if btn.count() > 0:
                btn.first.click(timeout=5_000)
                return True
        except Exception:
            pass
    return False
