"""
Iowa Lottery scratch-off scraper.

Three sources combined:
  ScratchGamesListing.aspx  — game IDs, names, image URLs
  RemainingPrizes.aspx      — prize tiers with claimed/unclaimed counts + ticket price
  ScratchGamesDetail.aspx   — per-tier odds + overall odds (fetched concurrently)

All three pages render as static HTML (no JS execution required).
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

_LISTING_URL = "https://ialottery.com/Pages/Games-Scratch/ScratchGamesListing.aspx"
_REMAINING_URL = "https://ialottery.com/Pages/Games/RemainingPrizes.aspx"
_DETAIL_URL = "https://ialottery.com/Pages/Games-Scratch/ScratchGamesDetail.aspx?g={}"
_IMG_BASE = "https://ialottery.com/images/ScratchGameImages/"


class IowaScraper(BaseScraper):
    state_code = "IA"
    state_name = "Iowa"
    base_url = "https://ialottery.com"

    def scrape(self) -> list[dict]:
        listing = self._scrape_listing()
        logger.info("IA: %d games in listing", len(listing))

        remaining = self._scrape_remaining()
        logger.info("IA: remaining data for %d games", len(remaining))

        # Fetch all detail pages concurrently
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self._scrape_detail, gid): gid for gid in listing}
            details: dict[str, dict] = {}
            for fut in as_completed(futures):
                gid = futures[fut]
                try:
                    details[gid] = fut.result()
                except Exception as exc:
                    logger.warning("IA: detail failed for game %s: %s", gid, exc)

        games = []
        for gid, info in listing.items():
            rem = remaining.get(gid, {})
            rem_tiers = rem.get("tiers", [])
            price = rem.get("price") or info.get("price") or 1.0
            detail = details.get(gid, {})
            odds_map: dict[float, float] = detail.get("odds_map", {})
            overall_odds: float | None = detail.get("overall_odds")

            tiers = []
            total_w = 0
            rem_w = 0
            for t in rem_tiers:
                pa = t["prize_amount"]
                pr = t["prizes_remaining"]
                pt = t["prizes_total"]
                tiers.append({
                    "prize_amount": pa,
                    "odds_one_in": odds_map.get(pa),
                    "prizes_remaining": pr,
                    "prizes_total": pt,
                })
                if pt:
                    total_w += pt
                if pr:
                    rem_w += pr

            total_tickets = int(total_w * overall_odds) if (overall_odds and total_w) else None
            tickets_remaining = int(rem_w * overall_odds) if (overall_odds and rem_w) else None

            games.append(self.build_game(
                game_id=f"ia{gid}",
                name=info["name"],
                price=price,
                tiers=tiers,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                detail_url=_DETAIL_URL.format(gid),
                image_url=info.get("image_url"),
                overall_odds=overall_odds,
            ))

        logger.info("IA: built %d games", len(games))
        return games

    # ── Listing page ───────────────────────────────────────────────────────────

    def _scrape_listing(self) -> dict[str, dict]:
        """Returns {game_id: {name, image_url}}."""
        soup = self.soup(_LISTING_URL)
        result: dict[str, dict] = {}

        for a in soup.find_all("a", href=re.compile(r"ScratchGamesDetail", re.I)):
            m = re.search(r"[?&]g=(\d+)", a.get("href", ""))
            if not m:
                continue
            gid = m.group(1)
            if gid in result:
                continue

            img = a.find("img")
            if img:
                src = img.get("src", "")
                filename = src.rsplit("/", 1)[-1]
                img_url = _IMG_BASE + filename
                name = img.get("alt", "").strip()
            else:
                img_url = None
                name = ""

            if not name:
                name = a.get_text(strip=True)
            # Strip "(NEW!)" suffix
            name = re.sub(r"\s*\(NEW!\)\s*", "", name, flags=re.IGNORECASE).strip()

            result[gid] = {"name": name, "image_url": img_url}

        return result

    # ── Remaining prizes page ──────────────────────────────────────────────────

    def _scrape_remaining(self) -> dict[str, dict]:
        """Returns {game_id: {price, tiers: [{prize_amount, prizes_remaining, prizes_total}]}}."""
        soup = self.soup(_REMAINING_URL)
        table = soup.find("table")
        if not table:
            logger.warning("IA: no table found on RemainingPrizes page")
            return {}

        result: dict[str, dict] = {}
        current_id: str | None = None
        current_is_scratch = False

        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            texts = [c.get_text(strip=True) for c in cells]

            # Game header row: first cell contains "(NNN)" game number
            gm = re.search(r"\((\d+)\)", texts[0])
            if gm:
                current_id = gm.group(1)
                game_type = texts[1] if len(texts) > 1 else ""
                current_is_scratch = "Scratch" in game_type
                if current_is_scratch:
                    price = self._parse_price(texts[2] if len(texts) > 2 else "")
                    result[current_id] = {"price": price, "tiers": []}
                    # First prize tier may be on this same row (cols 3/4/5)
                    if len(texts) >= 6:
                        self._add_tier(result[current_id]["tiers"], texts[3], texts[4], texts[5])
                continue

            # Continuation rows: prize | claimed | unclaimed (rowspan hides game cols)
            if current_id and current_is_scratch and len(texts) >= 3:
                self._add_tier(result[current_id]["tiers"], texts[0], texts[1], texts[2])

        return result

    def _parse_price(self, text: str) -> float:
        m = re.search(r"\$(\d+)", text)
        return float(m.group(1)) if m else 1.0

    def _add_tier(self, tiers: list, prize_text: str, claimed_text: str, unclaimed_text: str):
        try:
            prize_amt = parse_prize_amount(prize_text)
            if not prize_amt or prize_amt <= 0:
                return
            claimed = int(claimed_text.replace(",", ""))
            unclaimed = int(unclaimed_text.replace(",", ""))
            tiers.append({
                "prize_amount": prize_amt,
                "prizes_remaining": unclaimed,
                "prizes_total": claimed + unclaimed,
            })
        except (ValueError, TypeError):
            pass

    # ── Detail page (odds) ─────────────────────────────────────────────────────

    def _scrape_detail(self, gid: str) -> dict:
        """Returns {odds_map: {prize_amount: odds_one_in}, overall_odds: float}."""
        soup = self.soup(_DETAIL_URL.format(gid))
        page_text = soup.get_text(" ", strip=True)

        # Overall odds: look for "overall" near "1 in X"
        overall_odds = None
        m = re.search(r"overall[^0-9]{0,40}1\s+in\s+([\d,\.]+)", page_text, re.IGNORECASE)
        if m:
            try:
                overall_odds = float(m.group(1).replace(",", ""))
            except ValueError:
                pass

        # Prize → odds table
        odds_map: dict[float, float] = {}
        for tbl in soup.find_all("table"):
            hdrs = " ".join(th.get_text(strip=True).lower() for th in tbl.find_all("th"))
            if "prize" not in hdrs or "odds" not in hdrs:
                continue
            for row in tbl.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                prize_amt = parse_prize_amount(cells[0].get_text(strip=True))
                odds = parse_odds(cells[1].get_text(strip=True))
                if prize_amt and odds:
                    odds_map[prize_amt] = odds
            if odds_map:
                break

        return {"odds_map": odds_map, "overall_odds": overall_odds}
