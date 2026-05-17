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

            # Build lookup: prize_amount -> {prizes_remaining, prizes_total}
            rem_map = {t["prize_amount"]: t for t in rem_tiers}

            # Estimate total_tickets from tiers that have BOTH remaining counts AND odds.
            # (RemainingPrizes only tracks top/large prize tiers, so we can't just sum
            #  prizes_total — that severely underestimates total tickets.)
            ticket_estimates = []
            for t in rem_tiers:
                odds = odds_map.get(t["prize_amount"])
                if odds and t["prizes_total"]:
                    ticket_estimates.append(t["prizes_total"] * odds)
            total_tickets = None
            if ticket_estimates:
                total_tickets = int(sorted(ticket_estimates)[len(ticket_estimates) // 2])
            elif overall_odds:
                total_w = sum(t["prizes_total"] or 0 for t in rem_tiers)
                if total_w:
                    total_tickets = int(total_w * overall_odds)

            # Overall depletion rate from tracked tiers (the large prizes)
            total_w = sum(t["prizes_total"] or 0 for t in rem_tiers)
            rem_w = sum(t["prizes_remaining"] or 0 for t in rem_tiers)
            depletion = max(0.0, min(1.0, (total_w - rem_w) / total_w)) if total_w > 0 else None

            tickets_remaining = int(total_tickets * (1 - depletion)) if (total_tickets and depletion is not None) else None

            # Build full tier list from detail-page odds (covers ALL prize levels).
            # Tiers tracked in RemainingPrizes get actual remaining counts;
            # smaller tiers not tracked there get counts estimated via depletion.
            tiers = []
            for pa, odds in sorted(odds_map.items(), reverse=True):
                rem_t = rem_map.get(pa)
                if rem_t:
                    pr = rem_t["prizes_remaining"]
                    pt = rem_t["prizes_total"]
                else:
                    pt = int(total_tickets / odds) if (total_tickets and odds) else None
                    pr = int(pt * (1 - depletion)) if (pt and depletion is not None) else None
                tiers.append({
                    "prize_amount": pa,
                    "odds_one_in": odds,
                    "prizes_remaining": pr,
                    "prizes_total": pt,
                })

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
                ev_approximate=True,
            ))

        logger.info("IA: built %d games", len(games))
        return games

    # ── Listing page ───────────────────────────────────────────────────────────

    def _scrape_listing(self) -> dict[str, dict]:
        """Returns {game_id: {name, price, image_url}}.

        Anchor text format: '$50500X' (price immediately followed by name),
        so the first $N captures the ticket price.
        """
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
                # Alt text format: "GAME NAME scratch ticket"
                name = re.sub(r"\s+scratch ticket\s*$", "", img.get("alt", ""), flags=re.IGNORECASE).strip()
            else:
                img_url = None
                name = ""

            # Price is the first $N in the anchor text (e.g. "$30Ruby Red Crossword")
            anchor_text = a.get_text(strip=True)
            pm = re.match(r"\$(\d+)", anchor_text)
            price = float(pm.group(1)) if pm else 1.0

            if not name:
                name = re.sub(r"^\$\d+", "", anchor_text).strip()
            # Strip "(NEW!)" if present
            name = re.sub(r"\s*\(NEW!\)\s*", "", name, flags=re.IGNORECASE).strip()

            result[gid] = {"name": name, "price": price, "image_url": img_url}

        return result

    # ── Remaining prizes page ──────────────────────────────────────────────────

    def _scrape_remaining(self) -> dict[str, dict]:
        """Returns {game_id: {price, tiers: [{prize_amount, prizes_remaining, prizes_total}]}}.

        Every row has 6 cells (game name/type/cost repeated per tier — no rowspan):
          [0] GameName (ID)  [1] Type  [2] Cost  [3] Prize  [4] Claimed  [5] Unclaimed
        """
        soup = self.soup(_REMAINING_URL)
        table = soup.find("table")
        if not table:
            logger.warning("IA: no table found on RemainingPrizes page")
            return {}

        result: dict[str, dict] = {}

        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 6:
                continue
            texts = [c.get_text(strip=True) for c in cells]

            # Filter to Scratch only
            if texts[1] != "Scratch":
                continue

            gm = re.search(r"\((\d+)\)", texts[0])
            if not gm:
                continue
            gid = gm.group(1)

            if gid not in result:
                try:
                    price = float(texts[2])
                except ValueError:
                    price = 1.0
                result[gid] = {"price": price, "tiers": []}

            self._add_tier(result[gid]["tiers"], texts[3], texts[4], texts[5])

        return result

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
