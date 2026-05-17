"""
Pennsylvania Lottery scratch-off scraper.
Source: https://www.palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx
  Single page: Game# | Name | Price | Top Six Prizes | Wins Remaining

EV calculation strategy:
  - Per-game "Chances of Winning" PDFs contain a prize-level odds column.
  - Detail pages also expose overall odds (e.g. "1:4.32").
  - Detail pages + PDFs are fetched concurrently (10 workers); PDFs only for
    games not already in cache so repeat runs are fast.
  - Cache stores {game_id: {"tiers": [...], "overall_odds": float}} in
    pa_odds_cache_v2.json (old list-format entries still readable).
  - tickets_remaining = median(prizes_remaining_i * original_odds_i) across
    top-6 tiers with both data points.
  - Hybrid EV: prize*(prizes_remaining/tickets_remaining) for tiers with
    live remaining data; prize/original_odds for smaller tiers.
"""
import io
import json
import logging
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup

from backend.scraper.base import BaseScraper, HEADERS
from backend.ev_calculator import (
    parse_prize_amount,
    calculate_ev,
    calculate_jackpot_odds,
    find_top_prize,
)

logger = logging.getLogger(__name__)

LIST_URL = "https://www.palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx"
BASE_URL = "https://www.palottery.pa.gov"
CACHE_FILE = Path(__file__).parent / "pa_odds_cache_v2.json"

_CONCURRENCY = 10
_DETAIL_TIMEOUT = 25


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


def _cache_get(cache: dict, gid: str) -> tuple[list[dict], float | None]:
    """Return (tiers, overall_odds) from cache entry. Handles both old list format and new dict format."""
    entry = cache.get(gid)
    if entry is None:
        return [], None
    if isinstance(entry, list):
        return entry, None
    return entry.get("tiers", []), entry.get("overall_odds")


# ── PDF parsing ───────────────────────────────────────────────────────────────

def _parse_pdf_odds(pdf_bytes: bytes) -> list[dict]:
    """Extract consolidated prize odds from a PA Lottery 'Chances of Winning' PDF.

    The rightmost column has entries like:
        $1 = 10.10
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

    # pdfplumber injects spaces inside large numbers due to PDF kerning, e.g.:
    #   "$5,000= 6 00,000" → real odds are 600,000
    pattern = r"\$([\d,]+(?:\.\d+)?)\s*[=:]\s*([\d][\d,\. ]*)"
    seen: set[float] = set()
    tiers: list[dict] = []
    for m in re.finditer(pattern, text):
        prize = parse_prize_amount("$" + m.group(1))
        try:
            odds_raw = m.group(2).strip()
            odds = float(odds_raw.replace(" ", "").replace(",", ""))
        except ValueError:
            continue
        if prize and prize > 0 and odds > 0 and prize not in seen:
            seen.add(prize)
            tiers.append({"prize_amount": prize, "odds_one_in": odds})

    return sorted(tiers, key=lambda t: t["prize_amount"], reverse=True)


# ── parallel detail-page + PDF fetcher ───────────────────────────────────────

def _fetch_game_odds(game_info: dict) -> tuple[str, list[dict], float | None]:
    """Fetch detail page then PDF for one game. Thread-safe (own requests calls).

    Returns (game_id, tiers_from_pdf, overall_odds_from_page).
    """
    gid = game_info["game_id"]
    detail_url = game_info["detail_url"]

    if not detail_url:
        return gid, [], None

    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=_DETAIL_TIMEOUT)
        resp.raise_for_status()
        dsoup = BeautifulSoup(resp.text, "lxml")

        # Overall odds from page (e.g. "1:4.32" in <p class="table-disclaimer">)
        overall_odds = None
        for p in dsoup.find_all("p", class_="table-disclaimer"):
            m = re.search(r"1\s*[:/]\s*(\d+(?:\.\d+)?)", p.get_text())
            if m:
                try:
                    overall_odds = float(m.group(1))
                except ValueError:
                    pass
                break

        # PDF link
        pdf_href = None
        for atag in dsoup.find_all("a", href=True):
            h = atag["href"]
            if "_DATA.pdf" in h and "uploadedfiles" in h.lower():
                pdf_href = h
                break
        if not pdf_href:
            for atag in dsoup.find_all("a", href=True):
                if atag["href"].lower().endswith(".pdf"):
                    pdf_href = atag["href"]
                    break

        if not pdf_href:
            logger.debug("PA %s: no PDF link on detail page", gid)
            return gid, [], overall_odds

        pdf_url = (BASE_URL + pdf_href) if pdf_href.startswith("/") else pdf_href
        pdf_resp = requests.get(pdf_url, headers=HEADERS, timeout=_DETAIL_TIMEOUT)
        pdf_resp.raise_for_status()

        tiers = _parse_pdf_odds(pdf_resp.content)
        logger.debug("PA %s: %d PDF tiers, overall_odds=%s", gid, len(tiers), overall_odds)
        return gid, tiers, overall_odds

    except Exception as e:
        logger.warning("PA %s: detail/PDF fetch error: %s", gid, e)
        return gid, [], None


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
    scraper_timeout = 900

    def scrape(self) -> list[dict]:
        soup = self.soup(LIST_URL)

        table = soup.find("table", id="remaining-prizes") or None
        if not table:
            for t in soup.find_all("table"):
                txt = t.get_text().lower()
                if "game" in txt and "prize" in txt and "remaining" in txt:
                    table = t
                    break
        if not table:
            logger.warning("PA: prizes remaining table not found")
            return []

        # ── pass 1: parse game rows ─────────────────────────────────────────
        raw: list[dict] = []
        seen: set[str] = set()

        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 5:
                continue

            # Game number from data-order or span.new-game
            game_id_raw = cells[0].get("data-order")
            if not game_id_raw:
                span = cells[0].find("span", class_="new-game")
                game_id_raw = span.get_text(strip=True) if span else cells[0].get_text(strip=True)
            game_id = re.sub(r"[^\d]", "", str(game_id_raw))
            if not game_id or game_id in seen:
                continue
            seen.add(game_id)

            # Game name link — first <a> only (second may be second-chance icon)
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

        # ── pass 2: parallel fetch for uncached games ────────────────────────
        cache = _load_cache()
        cache_updated = False
        overall_odds_map: dict[str, float] = {}

        need_fetch = [g for g in raw if g["game_id"] not in cache and g["detail_url"]]
        logger.info("PA: %d games need detail/PDF fetch (%d already cached)",
                    len(need_fetch), len(raw) - len(need_fetch))

        if need_fetch:
            with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
                future_to_gid = {
                    executor.submit(_fetch_game_odds, g): g["game_id"]
                    for g in need_fetch
                }
                for future in as_completed(future_to_gid):
                    gid, tiers, overall_odds = future.result()
                    if tiers:
                        cache[gid] = {"tiers": tiers, "overall_odds": overall_odds}
                        cache_updated = True
                    if overall_odds is not None:
                        overall_odds_map[gid] = overall_odds

        if cache_updated:
            _save_cache(cache)

        # ── pass 3: build game dicts with hybrid EV ──────────────────────────
        games: list[dict] = []
        for g in raw:
            gid   = g["game_id"]
            price = g["price"]
            top6  = g["top6"]

            pdf_tiers, overall_odds = _cache_get(cache, gid)
            if overall_odds is None:
                overall_odds = overall_odds_map.get(gid)

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

            # Estimate tickets_remaining via median(prizes_remaining * odds) across
            # tiers with both values; skip depleted (0) tiers to avoid skewing low.
            estimates = [
                t["prizes_remaining"] * t["odds_one_in"]
                for t in all_tiers
                if t.get("prizes_remaining") and t.get("odds_one_in")
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
                "overall_odds_one_in": overall_odds,
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
