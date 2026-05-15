"""
Pennsylvania Lottery scratch-off scraper.
Source: https://www.palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx
  Single page: Game# | Name | Price | Top Six Prizes | Wins Remaining

EV calculation strategy:
  - Per-game "Chances of Winning" PDFs contain a "CONSOLIDATED CHANCES ARE 1 IN" column
    with entries like "$1 = 10.10" and "$2,500 = 420,000" — one row per distinct prize level.
  - We extract these via regex on the raw PDF text (pdfplumber extract_text).
  - Odds are cached in pa_odds_cache.json; new games fetch on first appearance.
  - tickets_remaining = median(prizes_remaining_i * original_odds_i) across top-6 tiers.
  - Hybrid EV: prize * (prizes_remaining / tickets_remaining) for top-6 tiers;
    prize / original_odds for all other (smaller) tiers.
"""
import io
import json
import logging
import re
import statistics
from pathlib import Path

import pdfplumber

from backend.scraper.base import BaseScraper
from backend.ev_calculator import (
    parse_prize_amount,
    calculate_ev,
    calculate_jackpot_odds,
    find_top_prize,
)

logger = logging.getLogger(__name__)

LIST_URL = "https://www.palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx"
BASE_URL = "https://www.palottery.pa.gov"
CACHE_FILE = Path(__file__).parent / "pa_odds_cache.json"


# ── cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("PA: could not save odds cache: %s", e)


# ── PDF parsing ───────────────────────────────────────────────────────────────

def _parse_pdf_odds(pdf_bytes: bytes) -> list[dict]:
    """Extract consolidated prize odds from a PA Lottery 'Chances of Winning' PDF.

    The rightmost column has entries like:
        $1 = 10.10
        $2 = 13.89
        $2,500 = 420,000
    We find these with a regex on the full extracted text.
    Returns [{prize_amount, odds_one_in}] sorted descending by prize.
    """
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
    except Exception as e:
        logger.warning("PA: pdfplumber error: %s", e)
        return []

    # Match "$AMOUNT = ODDS" — only present in the CONSOLIDATED CHANCES column
    pattern = r"\$([\d,]+(?:\.\d+)?)\s*=\s*([\d,]+(?:\.\d+)?)"
    seen: set[float] = set()
    tiers: list[dict] = []
    for m in re.finditer(pattern, text):
        prize = parse_prize_amount("$" + m.group(1))
        try:
            odds = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if prize and prize > 0 and odds > 0 and prize not in seen:
            seen.add(prize)
            tiers.append({"prize_amount": prize, "odds_one_in": odds})

    return sorted(tiers, key=lambda t: t["prize_amount"], reverse=True)


# ── hybrid EV ─────────────────────────────────────────────────────────────────

def _hybrid_ev(price: float, all_tiers: list[dict], tickets_remaining: float) -> dict:
    """EV using prizes_remaining for tiers that have it, original odds for the rest."""
    total = 0.0
    for t in all_tiers:
        prize = t.get("prize_amount") or 0
        if prize <= 0:
            continue
        rem = t.get("prizes_remaining")
        if rem is not None:
            total += prize * (rem / tickets_remaining)
        else:
            odds = t.get("odds_one_in")
            if odds and odds > 0:
                total += prize / odds
    if total <= 0:
        return {"ev": None, "return_pct": None}
    return {
        "ev": round(total - price, 4),
        "return_pct": round((total / price) * 100, 2),
    }


# ── scraper ───────────────────────────────────────────────────────────────────

class PennsylvaniaScraper(BaseScraper):
    state_code = "PA"
    state_name = "Pennsylvania"
    base_url = BASE_URL
    # First run fetches ~184 detail pages + PDFs; cached runs are fast.
    scraper_timeout = 900

    def scrape(self) -> list[dict]:
        soup = self.soup(LIST_URL)

        table = None
        for t in soup.find_all("table"):
            txt = t.get_text().lower()
            if "game" in txt and "prize" in txt and "remaining" in txt:
                table = t
                break
        if not table:
            logger.warning("PA: prizes remaining table not found")
            return []

        # ── pass 1: collect game rows from prizes-remaining page ─────────────
        raw: list[dict] = []
        seen: set[str] = set()

        for row in table.find_all("tr")[1:]:
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

            prize_divs = cells[3].find_all("div")
            rem_divs   = cells[4].find_all("div")
            top6: dict[float, int | None] = {}
            for pd, rd in zip(prize_divs, rem_divs):
                p = parse_prize_amount(pd.get_text(strip=True))
                try:
                    r = int(rd.get_text(strip=True).replace(",", ""))
                except (ValueError, TypeError):
                    r = None
                if p and p > 0:
                    top6[p] = r

            raw.append({
                "game_id":    game_id,
                "name":       name,
                "price":      price,
                "detail_url": detail_url,
                "top6":       top6,
            })

        logger.info("PA: %d games from prizes-remaining page", len(raw))
        if not raw:
            return []

        # ── pass 2: fetch PDFs for games not yet in cache ────────────────────
        cache = _load_cache()
        cache_updated = False

        for g in raw:
            gid = g["game_id"]
            if gid in cache:
                continue
            if not g["detail_url"]:
                continue
            try:
                dsoup = self.soup(g["detail_url"])
                pdf_href = None
                for atag in dsoup.find_all("a", href=True):
                    h = atag["href"]
                    if "_DATA.pdf" in h and "uploadedfiles" in h.lower():
                        pdf_href = h
                        break
                if not pdf_href:
                    # fallback: any PDF link
                    for atag in dsoup.find_all("a", href=True):
                        h = atag["href"]
                        if h.lower().endswith(".pdf"):
                            pdf_href = h
                            break
                if not pdf_href:
                    logger.debug("PA %s: no PDF link found", gid)
                    continue

                pdf_url = (BASE_URL + pdf_href) if pdf_href.startswith("/") else pdf_href
                tiers = _parse_pdf_odds(self.get(pdf_url).content)
                if tiers:
                    cache[gid] = tiers
                    cache_updated = True
                    logger.debug("PA %s: cached %d tiers", gid, len(tiers))
                else:
                    logger.debug("PA %s: PDF yielded 0 tiers", gid)
            except Exception as e:
                logger.warning("PA %s: PDF fetch/parse error: %s", gid, e)

        if cache_updated:
            _save_cache(cache)

        # ── pass 3: build game dicts with hybrid EV ──────────────────────────
        games: list[dict] = []
        for g in raw:
            gid   = g["game_id"]
            price = g["price"]
            top6  = g["top6"]  # {prize_amount: prizes_remaining}

            pdf_tiers: list[dict] = cache.get(gid, [])

            # Merge PDF odds with current prizes_remaining from HTML
            if pdf_tiers:
                all_tiers = [
                    {
                        "prize_amount":    t["prize_amount"],
                        "odds_one_in":     t["odds_one_in"],
                        "prizes_remaining": top6.get(t["prize_amount"]),
                        "prizes_total":    None,
                    }
                    for t in pdf_tiers
                ]
            else:
                all_tiers = [
                    {
                        "prize_amount":    pa,
                        "odds_one_in":     None,
                        "prizes_remaining": rem,
                        "prizes_total":    None,
                    }
                    for pa, rem in top6.items()
                ]

            # Estimate tickets_remaining from top-6 tiers that have both data points
            estimates = [
                t["prizes_remaining"] * t["odds_one_in"]
                for t in all_tiers
                if t.get("prizes_remaining") is not None and t.get("odds_one_in")
            ]
            tickets_remaining = statistics.median(estimates) if estimates else None

            if tickets_remaining and pdf_tiers:
                ev_data = _hybrid_ev(price, all_tiers, tickets_remaining)
            elif pdf_tiers:
                ev_data = calculate_ev(price, all_tiers)
            else:
                ev_data = {"ev": None, "return_pct": None}

            top_prize, top_prize_remaining = find_top_prize(all_tiers)
            jackpot_odds = calculate_jackpot_odds(all_tiers, tickets_remaining)

            games.append({
                "game_id":             gid,
                "name":                g["name"],
                "price":               price,
                "ev":                  ev_data["ev"],
                "return_pct":          ev_data["return_pct"],
                "overall_odds_one_in": None,
                "top_prize":           top_prize,
                "top_prize_remaining": top_prize_remaining,
                "jackpot_odds_one_in": jackpot_odds,
                "total_tickets":       None,
                "tickets_remaining":   round(tickets_remaining) if tickets_remaining else None,
                "detail_url":          g["detail_url"],
                "image_url":           None,
                "end_date":            None,
                "tiers":               all_tiers,
            })

        with_ev = sum(1 for g in games if g["ev"] is not None)
        logger.info("PA: %d games, %d with EV", len(games), with_ev)
        return games
