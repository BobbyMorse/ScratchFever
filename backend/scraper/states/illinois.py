"""
Illinois Lottery scratch-off scraper.
Source: https://www.illinoislottery.com/about-the-games/unpaid-instant-games-prizes

The page is Cloudflare-protected; Playwright renders it fully.
Table structure: prize-amount columns as <th> headers, plus "Overall Odds", "Total",
and "Unclaimed" columns. Each row is one active game.

EV formula (same as MA):
  tickets_remaining = overall_odds × Σ prizes_remaining_i
  EV = Σ(prize_i × prizes_remaining_i) / tickets_remaining − price
"""
import re
import logging
from backend.scraper.playwright_base import PlaywrightScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

PRIZES_URL = "https://www.illinoislottery.com/about-the-games/unpaid-instant-games-prizes"
GAMES_HUB_URL = "https://www.illinoislottery.com/games-hub/instant-tickets"
BASE_URL = "https://www.illinoislottery.com"
# Game image asset filenames embed the IL game number, e.g.
# ".../26-0245_WebApp_INT_NewTicketAsset_Feb26_TicketLogos_IL-7645_Logo.png".
_IL_IMG_GAMEID_RE = re.compile(r"IL-(\d{3,6})_", re.IGNORECASE)


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
        # Cloudflare interstitial occasionally outlasts a single wait; retry
        # once with a longer timeout before giving up. The runner's site-outage
        # check preserves existing data if both attempts fail.
        soup = None
        last_err: Exception | None = None
        for attempt, (sel_timeout, extra_wait) in enumerate([(60_000, 2_000), (120_000, 4_000)]):
            try:
                soup = self.pw_soup(
                    PRIZES_URL,
                    wait_for="domcontentloaded",
                    selector=".unclaimed-prizes-table__row",
                    timeout=sel_timeout,
                    extra_wait_ms=extra_wait,
                )
                break
            except Exception as e:
                last_err = e
                logger.warning("IL: attempt %d failed waiting for table: %s", attempt + 1, e)
                self._close_browser()  # force a fresh Cloudflare handshake
        if soup is None:
            raise RuntimeError(f"IL: table never rendered after retries — {last_err}")

        # Game images live on a separate JS-rendered page; sniff network
        # responses on the games-hub URL and key images by the IL-XXXX game
        # number embedded in the asset filename.
        image_map = self._sniff_game_images()
        logger.info("IL: sniffed %d game images from games-hub", len(image_map))

        # ── Build header → column-index map ──────────────────────────────────
        headers = soup.select("th.unclaimed-prizes-table__header, th.unclaimed-prizes-table__cell")
        if not headers:
            headers = soup.select(".unclaimed-prizes-table th")

        header_texts = [h.get_text(" ", strip=True) for h in headers]
        logger.debug("IL headers: %s", header_texts)

        prize_cols: list[tuple[int, float]] = []
        total_col: int | None = None
        unclaimed_col: int | None = None
        odds_col: int | None = None

        for i, text in enumerate(header_texts):
            low = text.lower()
            if "unclaimed" in low or "remaining" in low:
                unclaimed_col = i
            elif "total" in low:
                total_col = i
            elif "odd" in low:
                odds_col = i
            else:
                amt = parse_prize_amount(text)
                if amt is not None and amt > 0:
                    prize_cols.append((i, amt))
                elif "free" in low and "ticket" in low:
                    prize_cols.append((i, 0.0))

        logger.info(
            "IL: %d prize columns, total_col=%s, unclaimed_col=%s, odds_col=%s",
            len(prize_cols), total_col, unclaimed_col, odds_col,
        )

        # ── Parse rows ────────────────────────────────────────────────────────
        games: list[dict] = []
        seen: set[str] = set()

        rows = soup.select("tr.unclaimed-prizes-table__row")
        logger.info("IL: found %d game rows", len(rows))

        for row in rows:
            # Use find_all("td") to capture every column, not just branded-class cells
            cells = row.find_all("td")
            if not cells:
                continue

            raw_name = cells[0].get_text(" ", strip=True)
            name = re.sub(r"\s*\(\s*\$[\d.]+\s*\)\s*$", "", raw_name).strip()
            if not name:
                continue

            price_attr = row.get("data-price")
            try:
                price = float(price_attr)
            except (TypeError, ValueError):
                m = re.search(r"\(\s*\$([\d.]+)\s*\)", raw_name)
                price = float(m.group(1)) if m else None
            if not price:
                continue

            game_id_raw = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
            game_id_m = re.search(r"(\d{3,6})", game_id_raw)
            game_id = game_id_m.group(1) if game_id_m else re.sub(r"[^a-z0-9]", "", name.lower())[:20]

            if game_id in seen:
                continue
            seen.add(game_id)

            # ── Extract overall odds ──────────────────────────────────────────
            overall_odds: float | None = None
            if odds_col is not None and len(cells) > odds_col:
                overall_odds = parse_odds(cells[odds_col].get_text(strip=True))
            if not overall_odds:
                overall_odds = parse_odds(row.get("data-odds", "") or "")

            # ── Build prize tiers ─────────────────────────────────────────────
            tiers: list[dict] = []

            # Strategy A: prize amounts are column headers, cells map 1-to-1
            if prize_cols and len(cells) > max(i for i, _ in prize_cols):
                for col_i, prize_amount in prize_cols:
                    if prize_amount <= 0:
                        continue
                    cell_text = cells[col_i].get_text(strip=True)
                    remaining = _parse_int(cell_text)
                    if remaining is not None:
                        total = None
                        if total_col is not None and len(cells) > total_col:
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

            # ── Compute tickets_remaining via MA formula ───────────────────────
            tickets_remaining: int | None = None
            total_tickets: int | None = None

            if tiers and overall_odds and overall_odds > 0:
                total_prizes_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
                total_prizes_printed = sum(t.get("prizes_total") or 0 for t in tiers)

                if total_prizes_remaining > 0:
                    tickets_remaining = round(overall_odds * total_prizes_remaining)
                if total_prizes_printed > 0:
                    total_tickets = round(overall_odds * total_prizes_printed)

                # Back-fill per-tier odds_one_in from total_tickets
                if total_tickets:
                    for t in tiers:
                        if t.get("prizes_total"):
                            t["odds_one_in"] = round(total_tickets / t["prizes_total"], 2)

            if not tiers:
                logger.debug("IL: no tiers parsed for game %s (%s)", name, game_id)
                games.append(self.build_game(
                    game_id=game_id,
                    name=name,
                    price=price,
                    tiers=[],
                    overall_odds=overall_odds,
                    detail_url=f"{BASE_URL}/games-hub/instant-tickets",
                ))
                continue

            games.append(self.build_game(
                game_id=game_id,
                name=name,
                price=price,
                tiers=tiers,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                overall_odds=overall_odds,
                detail_url=f"{BASE_URL}/games-hub/instant-tickets",
            ))

        logger.info("IL: %d games scraped", len(games))
        return games
