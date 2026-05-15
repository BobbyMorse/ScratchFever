"""
Illinois Lottery scratch-off scraper.
Source: https://www.illinoislottery.com/about-the-games/unpaid-instant-games-prizes

The page is Cloudflare-protected; Playwright renders it fully.
Table structure: scrollable prize-amount columns as <th> headers, with per-row
cells for each tier's remaining count. A "Total" cell (br-separated) and an
"Unclaimed" cell (br-separated) may also appear.

IL publishes remaining prize counts but NOT total tickets printed or per-game odds.
EV cannot be computed reliably; games will have ev=NULL and won't appear in rankings.
"""
import re
import logging
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount

logger = logging.getLogger(__name__)

PRIZES_URL = "https://www.illinoislottery.com/about-the-games/unpaid-instant-games-prizes"
BASE_URL = "https://www.illinoislottery.com"


def _parse_int(text: str) -> int | None:
    cleaned = text.replace(",", "").strip()
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return None


class IllinoisScraper(PlaywrightScraper):
    state_code = "IL"
    state_name = "Illinois"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        soup = self.pw_soup(
            PRIZES_URL,
            wait_for="networkidle",
            selector=".unclaimed-prizes-table__row",
            timeout=60_000,
            extra_wait_ms=2000,
        )

        # ── Build header → column-index map ──────────────────────────────────
        # Prize amounts appear as <th> cells; we capture their indices and dollar values.
        headers = soup.select("th.unclaimed-prizes-table__header, th.unclaimed-prizes-table__cell")
        if not headers:
            # Fallback: all th in the table
            headers = soup.select(".unclaimed-prizes-table th")

        header_texts = [h.get_text(" ", strip=True) for h in headers]
        logger.debug("IL headers: %s", header_texts)

        # Map column index → prize amount (for headers like "$2", "$500", "Free Ticket")
        prize_cols: list[tuple[int, float]] = []
        total_col: int | None = None
        unclaimed_col: int | None = None

        for i, text in enumerate(header_texts):
            low = text.lower()
            if "unclaimed" in low or "remaining" in low:
                unclaimed_col = i
            elif "total" in low:
                total_col = i
            else:
                amt = parse_prize_amount(text)
                if amt is not None and amt > 0:
                    prize_cols.append((i, amt))
                elif "free" in low and "ticket" in low:
                    prize_cols.append((i, 0.0))  # free ticket — $0 prize

        logger.info("IL: found %d prize columns, total_col=%s, unclaimed_col=%s",
                    len(prize_cols), total_col, unclaimed_col)

        # ── Parse rows ────────────────────────────────────────────────────────
        games: list[dict] = []
        seen: set[str] = set()

        rows = soup.select("tr.unclaimed-prizes-table__row")
        logger.info("IL: found %d game rows", len(rows))

        for row in rows:
            cells = row.select("td.unclaimed-prizes-table__cell")
            if not cells:
                continue

            # Name is in first cell; game detail in third cell
            raw_name = cells[0].get_text(" ", strip=True)
            # Strip price suffix like "(  $1  )" that IL sometimes appends
            name = re.sub(r"\s*\(\s*\$[\d.]+\s*\)\s*$", "", raw_name).strip()
            if not name:
                continue

            price_attr = row.get("data-price")
            try:
                price = float(price_attr)
            except (TypeError, ValueError):
                # Try parsing price from name suffix
                m = re.search(r"\(\s*\$([\d.]+)\s*\)", raw_name)
                price = float(m.group(1)) if m else None
            if not price:
                continue

            # Game number from cell[2] (format: "7647\n(11 weeks)")
            game_id_raw = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
            game_id_m = re.search(r"(\d{3,6})", game_id_raw)
            game_id = game_id_m.group(1) if game_id_m else re.sub(r"[^a-z0-9]", "", name.lower())[:20]

            if game_id in seen:
                continue
            seen.add(game_id)

            tiers: list[dict] = []

            # Strategy A: prize amounts are column headers, cells map 1-to-1
            if prize_cols and len(cells) > max(i for i, _ in prize_cols):
                for col_i, prize_amount in prize_cols:
                    if prize_amount <= 0:
                        continue  # skip free ticket tiers — zero EV contribution
                    cell_text = cells[col_i].get_text(strip=True)
                    remaining = _parse_int(cell_text)
                    if remaining is not None:
                        total = None
                        if total_col is not None and len(cells) > total_col:
                            # total_col may hold br-separated values; try matching position
                            total_parts = [
                                p.strip() for p in
                                cells[total_col].decode_contents().split("<br") if p.strip()
                            ]
                            idx = [i for i, _ in prize_cols].index(col_i)
                            if idx < len(total_parts):
                                total = _parse_int(re.sub(r"[^0-9,]", "", total_parts[idx]))
                        tiers.append({
                            "prize_amount": prize_amount,
                            "prizes_remaining": remaining,
                            "prizes_total": total,
                            "odds_one_in": None,
                        })

            # Strategy B: br-separated prize amounts in a dedicated cell
            if not tiers and len(cells) >= 2:
                # Look for a cell that contains dollar amounts separated by <br>
                for cell in cells[2:]:
                    raw_html = cell.decode_contents()
                    if "$" not in raw_html and "free" not in raw_html.lower():
                        continue
                    parts = re.split(r"<br\s*/?>", raw_html, flags=re.I)
                    amounts = []
                    for p in parts:
                        txt = re.sub(r"<[^>]+>", "", p).strip()
                        amt = parse_prize_amount(txt)
                        if amt is not None and amt > 0:
                            amounts.append(amt)
                    if not amounts:
                        continue
                    # Grab the last two cells for total / unclaimed br-separated counts
                    def _br_ints(c):
                        raw = c.decode_contents()
                        parts = re.split(r"<br\s*/?>", raw, flags=re.I)
                        result = []
                        for p in parts:
                            txt = re.sub(r"<[^>]+>", "", p).strip()
                            v = _parse_int(txt)
                            if v is not None:
                                result.append(v)
                        return result

                    totals = _br_ints(cells[-2]) if len(cells) >= 2 else []
                    remainings = _br_ints(cells[-1])
                    for j, amt in enumerate(amounts):
                        tiers.append({
                            "prize_amount": amt,
                            "prizes_remaining": remainings[j] if j < len(remainings) else None,
                            "prizes_total": totals[j] if j < len(totals) else None,
                            "odds_one_in": None,
                        })
                    break

            if not tiers:
                logger.debug("IL: no tiers parsed for game %s (%s)", name, game_id)
                # Still include the game so it appears in IL listings
                games.append(self.build_game(
                    game_id=game_id,
                    name=name,
                    price=price,
                    tiers=[],
                    detail_url=f"{BASE_URL}/games-hub/instant-tickets",
                ))
                continue

            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=tiers,
                detail_url=f"{BASE_URL}/games-hub/instant-tickets",
            ))

        logger.info("IL: %d games scraped", len(games))
        return games
