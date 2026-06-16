"""
Pennsylvania Lottery scratch-off scraper.
Source: https://www.palottery.pa.gov/Scratch-Offs/Prizes-Remaining.aspx
  Single page: Game# | Name | Price | Top Six Prizes | Wins Remaining

EV calculation strategy:
  - Each game's detail page links to a PA Bulletin page (pacodeandbulletin.gov)
    with a full HTML prize table (table.miscr): prize amount, odds, winners per
    print-run. PDFs were image-only and are no longer used.
  - Detail pages + bulletin pages are fetched concurrently (10 workers). Odds
    are cached in pa_odds_cache_v2.json so repeat runs only fetch new games.
  - Per-tier odds from bulletin → prizes_total for each tier; prizes_remaining
    for top-6 tiers come from the main prizes-remaining page.
  - tickets_remaining estimated from remaining_fraction = median(prizes_remaining_i
    / prizes_total_i) across matched top-6 tiers × total_tickets.
  - Hybrid EV: actual prizes_remaining for top-6 tiers; original odds for
    smaller tiers below the published top-6.
"""
import json
import logging
import re
import statistics
import time
from concurrent.futures import as_completed
from pathlib import Path

from backend.scraper._shared_pool import DETAIL_POOL

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

# PA's VIP Players Club hosts second-chance drawings. The public listing
# page (no login required) carries one img per active drawing with
# alt="<GAME NAME> Second-Chance Drawing". We parse that to a name set and
# match against scraped PA game names.
SECOND_CHANCE_LISTING_URL = "https://www.palottery.pa.gov/VIP-Players-Club/Public/Second-Chance-Drawings.aspx"
SECOND_CHANCE_PLAYER_URL = "https://www.palottery.pa.gov/VIP-Players-Club/Public/Second-Chance-Drawings.aspx"


def _norm_pa_name(s: str) -> str:
    s = s or ""
    s = s.replace("$", "").replace(",", "")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())

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


def _cache_get(cache: dict, gid: str) -> tuple[list[dict], float | None, int | None, str | None]:
    """Return (tiers, overall_odds, total_tickets, image_url) from cache entry.
    Handles legacy list format (tiers only) and current dict format."""
    entry = cache.get(gid)
    if entry is None:
        return [], None, None, None
    if isinstance(entry, list):
        return entry, None, None, None
    return (
        entry.get("tiers", []),
        entry.get("overall_odds"),
        entry.get("total_tickets"),
        entry.get("image_url"),
    )


# ── bulletin HTML parser ──────────────────────────────────────────────────────

def _parse_bulletin(html: str) -> tuple[list[dict], int | None]:
    """Parse PA Bulletin page (pacodeandbulletin.gov) prize table.

    Table class is 'miscr'. PA Lottery uses different column layouts per game type:
      4-col: [win_condition, prize_amount, odds, winners_per_run]
      5-col: [win_condition, bonus_condition, prize_amount, odds, winners_per_run]
      6-col: [win_cond, prize_set1, win_cond2, prize_set2, odds, winners_per_run]

    In every format: winners = last column, prize = rightmost parseable dollar
    amount before the last two columns. We derive odds from total_tickets / winners.

    Multiple rows share the same prize amount (different combos); we sum winners
    across combos and derive combined odds = total_tickets / total_winners.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="miscr")
    if not table:
        return [], None

    rows = table.find_all("tr")
    if not rows:
        return [], None

    # Parse total_tickets from <th> header rows only.
    # Some bulletins render "Per¹ 5,400,000 Tickets" (superscript footnote digit
    # between "Per" and the actual number); match any 7+ digit number before "Tickets".
    total_tickets = None
    for row in rows:
        if row.find("td"):
            break  # stop at first data row
        m = re.search(r"([\d,]{7,})\s*[Tt]ickets", row.get_text())
        if m:
            try:
                total_tickets = int(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

    # Group winners by prize amount (multiple combos → same prize)
    winners_by_prize: dict[float, int] = {}
    for row in rows[1:]:
        cells = row.find_all("td")
        n = len(cells)
        if n < 3:
            continue

        cell_texts = [c.get_text(strip=True) for c in cells]

        # Winners = last column (always positive integer, comma-formatted)
        winners_text = cell_texts[-1].replace(",", "")
        try:
            winners = int(float(winners_text))
        except (ValueError, TypeError):
            continue
        if winners <= 0:
            continue

        # Prize = rightmost dollar-parseable value in cols [0 .. n-3].
        # Handles plain amounts ("$2,500") and descriptions ("FREE $1 TICKET" → $1).
        prize = None
        for i in range(n - 3, -1, -1):
            text = cell_texts[i]
            p = parse_prize_amount(text)
            if p and p > 0:
                prize = p
                break
            # Extract dollar amount from descriptions like "FREE $1 TICKET"
            dm = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", text)
            if dm:
                p = parse_prize_amount("$" + dm.group(1).replace(",", ""))
                if p and p > 0:
                    prize = p
                    break

        if not prize:
            continue

        winners_by_prize[prize] = winners_by_prize.get(prize, 0) + winners

    if not winners_by_prize or not total_tickets:
        return [], total_tickets

    tiers = []
    for prize, total_winners in sorted(winners_by_prize.items(), reverse=True):
        odds = round(total_tickets / total_winners, 2) if total_winners else None
        tiers.append({
            "prize_amount": prize,
            "odds_one_in":  odds,
            "prizes_total": total_winners,
        })

    return tiers, total_tickets


# ── parallel detail + bulletin fetcher ───────────────────────────────────────

def _fetch_game_odds(
    game_info: dict,
) -> tuple[str, list[dict], float | None, int | None, str | None]:
    """Fetch detail page then PA Bulletin for one game. Thread-safe.

    Returns (game_id, tiers, overall_odds, total_tickets, image_url).
    """
    gid = game_info["game_id"]
    detail_url = game_info["detail_url"]

    if not detail_url:
        return gid, [], None, None, None

    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=_DETAIL_TIMEOUT)
        resp.raise_for_status()
        dsoup = BeautifulSoup(resp.text, "lxml")

        # Overall odds from detail page: <p class="table-disclaimer">1:4.32</p>
        overall_odds = None
        for p in dsoup.find_all("p", class_="table-disclaimer"):
            m = re.search(r"1\s*[:/]\s*(\d+(?:\.\d+)?)", p.get_text())
            if m:
                try:
                    overall_odds = float(m.group(1))
                except ValueError:
                    pass
                break

        # Image URL from detail page — prefer /uploadedimages/ (actual ticket art);
        # skip site chrome like /media/Logos/ and /Custom/img/
        image_url = None
        for img in dsoup.find_all("img"):
            src = img.get("src") or img.get("data-src", "")
            if not src or not re.search(r"\.(jpg|jpeg|png|webp)", src, re.I):
                continue
            if re.search(r"/(media/Logos|Custom/img)/", src, re.I):
                continue
            image_url = (BASE_URL + src) if src.startswith("/") else src
            break

        # PA Bulletin link ("Complete Game Rules")
        bulletin_url = None
        for atag in dsoup.find_all("a", href=True):
            if "pacodeandbulletin.gov" in atag["href"]:
                bulletin_url = atag["href"]
                break

        if not bulletin_url:
            logger.debug("PA %s: no PA Bulletin link on detail page", gid)
            return gid, [], overall_odds, None, image_url

        time.sleep(0.15)  # brief pause between requests to the two servers
        bull_resp = requests.get(bulletin_url, headers=HEADERS, timeout=_DETAIL_TIMEOUT)
        bull_resp.raise_for_status()

        tiers, total_tickets = _parse_bulletin(bull_resp.text)
        logger.debug(
            "PA %s: %d bulletin tiers, total_tickets=%s, overall_odds=%s",
            gid, len(tiers), total_tickets, overall_odds,
        )
        return gid, tiers, overall_odds, total_tickets, image_url

    except Exception as e:
        logger.warning("PA %s: fetch error: %s", gid, e)
        return gid, [], None, None, None


# ── hybrid EV ─────────────────────────────────────────────────────────────────

def _hybrid_ev(price: float, all_tiers: list[dict], tickets_remaining: float) -> dict:
    """EV using prizes_remaining for top-6 tiers (where available), original odds otherwise."""
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

    def _fetch_second_chance_set(self) -> set[str]:
        """Return normalized names of PA scratch games currently in a VIP
        second-chance drawing. Source: the public Second-Chance-Drawings
        listing, which renders one banner per active drawing with
        alt='<GAME NAME> Second-Chance Drawing'. If the fetch fails, return
        an empty set — better to under-report than blanket-flag."""
        try:
            resp = self.get(SECOND_CHANCE_LISTING_URL, timeout=20)
        except Exception as e:
            logger.warning("PA second-chance fetch failed: %s", e)
            return set()
        names = set()
        for m in re.finditer(r'alt="([^"]+?)\s+Second[\s-]?Chance\s+Drawing"', resp.text, re.I):
            normalized = _norm_pa_name(m.group(1))
            if normalized:
                names.add(normalized)
        return names

    def scrape(self) -> list[dict]:
        second_chance_names = self._fetch_second_chance_set()
        logger.info("PA second-chance eligible games: %d", len(second_chance_names))

        soup = self.soup(LIST_URL)

        table = soup.find("table", id="remaining-prizes")
        if not table:
            for t in soup.find_all("table"):
                txt = t.get_text().lower()
                if "game" in txt and "prize" in txt and "remaining" in txt:
                    table = t
                    break
        if not table:
            logger.warning("PA: prizes remaining table not found")
            return []

        # ── pass 1: parse game rows from prizes-remaining page ─────────────
        raw: list[dict] = []
        seen: set[str] = set()

        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 5:
                continue

            # Game number: prefer data-order on <td>, fall back to span.new-game
            game_id_raw = cells[0].get("data-order")
            if not game_id_raw:
                span = cells[0].find("span", class_="new-game")
                game_id_raw = span.get_text(strip=True) if span else cells[0].get_text(strip=True)
            game_id = re.sub(r"[^\d]", "", str(game_id_raw))
            if not game_id or game_id in seen:
                continue
            seen.add(game_id)

            # Game name — first <a> only (2nd may be second-chance / iLottery icon)
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

        # ── pass 2: parallel fetch odds for uncached games ─────────────────
        cache = _load_cache()
        cache_updated = False
        overall_odds_map: dict[str, float] = {}

        need_fetch = [
            g for g in raw
            if g["detail_url"] and (
                g["game_id"] not in cache
                or cache.get(g["game_id"], {}).get("image_url") is None
            )
        ]
        logger.info(
            "PA: %d games need bulletin fetch (%d already cached)",
            len(need_fetch), len(raw) - len(need_fetch),
        )

        if need_fetch:
            # Wave 1: parallel fetch (fast, but PA lottery server sometimes 500s under load)
            with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
                futures = {
                    executor.submit(_fetch_game_odds, g): g["game_id"]
                    for g in need_fetch
                }
                for future in as_completed(futures):
                    gid, tiers, overall_odds, total_tickets, image_url = future.result()
                    if tiers or image_url:
                        # Persist even if bulletin link is gone — the ticket
                        # image is still useful and may have been the only
                        # thing the fetch succeeded at.
                        existing = cache.get(gid) if isinstance(cache.get(gid), dict) else {}
                        cache[gid] = {
                            "tiers":         tiers or existing.get("tiers", []),
                            "overall_odds":  overall_odds if overall_odds is not None else existing.get("overall_odds"),
                            "total_tickets": total_tickets if total_tickets is not None else existing.get("total_tickets"),
                            "image_url":     image_url or existing.get("image_url"),
                        }
                        cache_updated = True
                    if overall_odds is not None:
                        overall_odds_map[gid] = overall_odds

            # Wave 2: sequential retry for games that 500'd in wave 1.
            # PA lottery server temporarily rate-limits with 500 (not 429) after
            # burst requests; wait 75s for it to recover before retrying.
            still_missing = [g for g in need_fetch if g["game_id"] not in cache]
            if still_missing:
                logger.info(
                    "PA: waiting 75s for server to recover, then retrying %d games",
                    len(still_missing),
                )
                time.sleep(75)
                for g in still_missing:
                    time.sleep(0.3)
                    gid, tiers, overall_odds, total_tickets, image_url = _fetch_game_odds(g)
                    if tiers or image_url:
                        existing = cache.get(gid) if isinstance(cache.get(gid), dict) else {}
                        cache[gid] = {
                            "tiers":         tiers or existing.get("tiers", []),
                            "overall_odds":  overall_odds if overall_odds is not None else existing.get("overall_odds"),
                            "total_tickets": total_tickets if total_tickets is not None else existing.get("total_tickets"),
                            "image_url":     image_url or existing.get("image_url"),
                        }
                        cache_updated = True
                    if overall_odds is not None:
                        overall_odds_map[gid] = overall_odds

        if cache_updated:
            _save_cache(cache)

        # ── pass 3: build game dicts with hybrid EV ────────────────────────
        games: list[dict] = []
        for g in raw:
            gid   = g["game_id"]
            price = g["price"]
            top6  = g["top6"]

            pdf_tiers, overall_odds, total_tickets, image_url = _cache_get(cache, gid)
            if overall_odds is None:
                overall_odds = overall_odds_map.get(gid)

            # Merge bulletin tiers (odds + prizes_total) with live prizes_remaining
            if pdf_tiers:
                all_tiers = [
                    {
                        "prize_amount":    t["prize_amount"],
                        "odds_one_in":     t.get("odds_one_in"),
                        "prizes_remaining": top6.get(t["prize_amount"]),
                        "prizes_total":    t.get("prizes_total"),
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

            # Estimate tickets_remaining via remaining fraction across matched top-6 tiers
            tickets_remaining = None
            if total_tickets:
                ratios = [
                    t["prizes_remaining"] / t["prizes_total"]
                    for t in all_tiers
                    if t.get("prizes_remaining") is not None
                    and t.get("prizes_total") and t["prizes_total"] > 0
                ]
                if ratios:
                    remaining_frac = statistics.median(ratios)
                    tickets_remaining = round(total_tickets * remaining_frac)

            # Fall back to median(prizes_remaining * odds_one_in) if no total_tickets
            if tickets_remaining is None:
                estimates = [
                    t["prizes_remaining"] * t["odds_one_in"]
                    for t in all_tiers
                    if t.get("prizes_remaining") and t.get("odds_one_in")
                ]
                if estimates:
                    tickets_remaining = round(statistics.median(estimates))

            if tickets_remaining and pdf_tiers:
                ev_data = _hybrid_ev(price, all_tiers, tickets_remaining)
            elif pdf_tiers:
                ev_data = calculate_ev(price, all_tiers)
            else:
                ev_data = {"ev": None, "return_pct": None}

            top_prize, top_prize_remaining = find_top_prize(all_tiers)
            jackpot_odds = calculate_jackpot_odds(all_tiers, tickets_remaining)

            name_norm = _norm_pa_name(g["name"])
            has_2c = name_norm in second_chance_names
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
                "total_tickets":       total_tickets,
                "tickets_remaining":   tickets_remaining,
                "detail_url":          g["detail_url"],
                "image_url":           image_url,
                "end_date":            None,
                "has_second_chance":   has_2c,
                "second_chance_url":   SECOND_CHANCE_PLAYER_URL if has_2c else None,
                "tiers":               all_tiers,
            })

        with_ev = sum(1 for g in games if g["ev"] is not None)
        logger.info("PA: %d games, %d with EV", len(games), with_ev)
        return games
