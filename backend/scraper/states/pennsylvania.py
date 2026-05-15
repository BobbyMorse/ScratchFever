"""
Pennsylvania Lottery scratch-off scraper.

Step 1 — prizes-remaining HTML (palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx):
  Gets top-6 prize tiers with prizes_remaining per game.

Step 2 — per-game detail page → PDF link:
  Each game detail page has a "Chances of Winning" PDF link.
  The PDF contains the full odds table (all tiers, $1 through top prize).

Step 3 — parse PDF with pdfplumber:
  Extract prize_amount + odds_one_in + prizes_total for every tier.

Merge: PDF provides full odds; HTML provides prizes_remaining for top-6 tiers.
Remaining tiers are estimated using the game's observed depletion rate.
"""
import re
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

LIST_URL = "https://www.palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx"
BASE_URL = "https://www.palottery.pa.gov"

MAX_WORKERS = 8   # parallel detail-page + PDF fetches
SCRAPE_DELAY = 0.15  # seconds between requests per worker


class PennsylvaniaScraper(BaseScraper):
    state_code = "PA"
    state_name = "Pennsylvania"
    base_url = BASE_URL
    scraper_timeout = 600

    def scrape(self) -> list[dict]:
        # ── Step 1: prizes-remaining page ────────────────────────────────
        soup = self.soup(LIST_URL)
        table = next(
            (t for t in soup.find_all("table")
             if "game" in t.get_text().lower() and "remaining" in t.get_text().lower()),
            None,
        )
        if not table:
            logger.warning("PA: prizes remaining table not found")
            return []

        rows = table.find_all("tr")
        game_stubs: list[dict] = []
        seen: set[str] = set()

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 5:
                continue

            game_id_raw = cells[0].get("data-order") or cells[0].get_text(strip=True)
            game_id = re.sub(r"[^\d]", "", str(game_id_raw))
            if not game_id or game_id in seen:
                continue
            seen.add(game_id)

            a = cells[1].find("a")
            name = a.get_text(strip=True) if a else cells[1].get_text(strip=True)
            name = re.sub(r"\s*\(PA[–\-‑]\d+\)\s*$", "", name).strip()
            href = a["href"] if a else None
            detail_url = (BASE_URL + href) if href and href.startswith("/") else href

            price_text = cells[2].get("data-order") or cells[2].get_text(strip=True)
            price = parse_prize_amount(str(price_text))
            if not price:
                continue

            # Top-6 remaining from HTML
            prize_divs = cells[3].find_all("div")
            rem_divs   = cells[4].find_all("div")
            top6: list[dict] = []
            for pd, rd in zip(prize_divs, rem_divs):
                prize = parse_prize_amount(pd.get_text(strip=True))
                try:
                    remaining = int(rd.get_text(strip=True).replace(",", ""))
                except (ValueError, TypeError):
                    remaining = None
                if prize and prize > 0:
                    top6.append({"prize_amount": prize, "prizes_remaining": remaining})

            game_stubs.append({
                "game_id": game_id,
                "name": name,
                "price": price,
                "detail_url": detail_url,
                "top6": top6,
            })

        logger.info("PA: %d games from prizes-remaining page", len(game_stubs))

        # ── Step 2 & 3: fetch detail pages + PDFs in parallel ────────────
        def enrich(stub: dict) -> dict:
            pdf_tiers = self._fetch_pdf_tiers(stub.get("detail_url"))
            stub["pdf_tiers"] = pdf_tiers
            time.sleep(SCRAPE_DELAY)
            return stub

        enriched: list[dict] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(enrich, s): s for s in game_stubs}
            for fut in as_completed(futures):
                try:
                    enriched.append(fut.result())
                except Exception as e:
                    stub = futures[fut]
                    logger.warning("PA: enrich failed for %s: %s", stub["name"], e)
                    stub["pdf_tiers"] = []
                    enriched.append(stub)

        # ── Step 4: build games ───────────────────────────────────────────
        games: list[dict] = []
        for stub in enriched:
            tiers = self._merge_tiers(stub["pdf_tiers"], stub["top6"])
            if not tiers:
                tiers = [
                    {"prize_amount": t["prize_amount"],
                     "prizes_remaining": t["prizes_remaining"],
                     "prizes_total": None,
                     "odds_one_in": None}
                    for t in stub["top6"]
                ]

            games.append(self.build_game(
                game_id=stub["game_id"],
                name=stub["name"],
                price=stub["price"],
                tiers=tiers,
                detail_url=stub["detail_url"],
            ))

        logger.info("PA: %d games built (%d with full PDF tiers)",
                    len(games), sum(1 for s in enriched if s["pdf_tiers"]))
        return games

    # ── helpers ───────────────────────────────────────────────────────────

    def _fetch_pdf_tiers(self, detail_url: str | None) -> list[dict]:
        """Visit detail page, find PDF link, download + parse it."""
        if not detail_url:
            return []
        try:
            detail_soup = self.soup(detail_url)
        except Exception as e:
            logger.debug("PA: detail page error %s: %s", detail_url, e)
            return []

        pdf_url = None
        for a in detail_soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()
            if href.lower().endswith(".pdf") and ("win" in text or "chance" in text or "odd" in text or "prize" in text or "data" in href.lower()):
                pdf_url = href if href.startswith("http") else BASE_URL + href
                break
        # Fallback: any PDF link containing "DATA" or "Odds"
        if not pdf_url:
            for a in detail_soup.find_all("a", href=True):
                href = a["href"]
                if href.lower().endswith(".pdf"):
                    pdf_url = href if href.startswith("http") else BASE_URL + href
                    break

        if not pdf_url:
            logger.debug("PA: no PDF found on %s", detail_url)
            return []

        try:
            resp = self.get(pdf_url)
        except Exception as e:
            logger.debug("PA: PDF download failed %s: %s", pdf_url, e)
            return []

        return self._parse_pdf(resp.content)

    def _parse_pdf(self, pdf_bytes: bytes) -> list[dict]:
        """Extract prize tiers from a PA lottery odds PDF using pdfplumber."""
        try:
            import pdfplumber
        except ImportError:
            logger.warning("PA: pdfplumber not installed — skipping PDF parse")
            return []

        tiers: list[dict] = []
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        parsed = self._parse_pdf_table(table)
                        if parsed:
                            tiers.extend(parsed)
                    if tiers:
                        break  # odds table is usually on page 1
        except Exception as e:
            logger.debug("PA: PDF parse error: %s", e)

        return tiers

    def _parse_pdf_table(self, table: list[list]) -> list[dict]:
        """Parse a pdfplumber table (list of rows) looking for prize/odds columns."""
        if not table or len(table) < 2:
            return []

        # Find header row: look for 'prize' and 'odd' keywords
        header_idx = None
        col_prize = col_odds = col_total = None
        for i, row in enumerate(table[:4]):
            if row is None:
                continue
            cells = [str(c or "").lower().strip() for c in row]
            has_prize = any("prize" in c or "amount" in c or "win" in c for c in cells)
            has_odds  = any("odd" in c or "chance" in c or "1 in" in c for c in cells)
            if has_prize or has_odds:
                header_idx = i
                for j, c in enumerate(cells):
                    if col_prize is None and ("prize" in c or "amount" in c or "win" in c):
                        col_prize = j
                    elif col_odds is None and ("odd" in c or "chance" in c or "1 in" in c):
                        col_odds = j
                    elif col_total is None and ("total" in c or "print" in c or "number" in c):
                        col_total = j
                break

        if header_idx is None or col_prize is None:
            return []

        tiers: list[dict] = []
        for row in table[header_idx + 1:]:
            if row is None:
                continue
            cells = [str(c or "").strip() for c in row]
            if len(cells) <= max(filter(lambda x: x is not None, [col_prize, col_odds, col_total or 0])):
                continue

            prize = parse_prize_amount(cells[col_prize]) if col_prize is not None else None
            if not prize or prize <= 0:
                continue

            odds = None
            if col_odds is not None and len(cells) > col_odds:
                odds_text = cells[col_odds]
                # PA PDFs often format as "1:X.XX" or "1 in X"
                odds_text = odds_text.replace("1:", "1 in ").replace("1 IN ", "1 in ")
                odds = parse_odds(odds_text)

            total = None
            if col_total is not None and len(cells) > col_total:
                try:
                    total = int(cells[col_total].replace(",", ""))
                except (ValueError, TypeError):
                    pass

            # Derive total from odds if we know total_tickets (we usually don't here)
            tiers.append({
                "prize_amount": prize,
                "odds_one_in": odds,
                "prizes_total": total,
                "prizes_remaining": None,  # filled in by merge step
            })

        return tiers

    def _merge_tiers(self, pdf_tiers: list[dict], top6: list[dict]) -> list[dict]:
        """
        Merge PDF full-odds tiers with HTML prizes_remaining for top-6 tiers.
        For tiers not in top-6, estimate prizes_remaining using depletion rate.
        """
        if not pdf_tiers:
            return []

        # Build lookup: prize_amount → prizes_remaining from HTML top-6
        html_remaining: dict[float, int | None] = {}
        for t in top6:
            html_remaining[t["prize_amount"]] = t.get("prizes_remaining")

        # Compute depletion rate from matched tiers (remaining / total)
        fracs: list[float] = []
        for t in pdf_tiers:
            pa = t["prize_amount"]
            rem = html_remaining.get(pa)
            tot = t.get("prizes_total")
            if rem is not None and tot and tot > 0:
                fracs.append(rem / tot)

        depletion_frac = (sum(fracs) / len(fracs)) if fracs else None

        merged: list[dict] = []
        for t in pdf_tiers:
            pa = t["prize_amount"]
            prizes_total = t.get("prizes_total")

            if pa in html_remaining:
                prizes_remaining = html_remaining[pa]
            elif depletion_frac is not None and prizes_total:
                prizes_remaining = max(0, int(prizes_total * depletion_frac))
            else:
                prizes_remaining = None

            merged.append({
                "prize_amount": pa,
                "odds_one_in": t.get("odds_one_in"),
                "prizes_total": prizes_total,
                "prizes_remaining": prizes_remaining,
            })

        return merged
