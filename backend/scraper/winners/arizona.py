"""
Arizona Lottery winners scraper.

The /winners/ page is Cloudflare-protected, so plain HTTP returns 403. We use
Playwright (already in requirements.txt) — same approach as the AZ games and
retailer scrapers. The page server-renders all ~460 most-recent winner cards
into the initial HTML; the "Load More" button just toggles CSS visibility,
so a single page load captures the full dataset.

Card shape (see scripts/_az_winners_probe.html):

    <div class="card winner" data-amount="10000-49999" data-year="2026"
         data-game="scratchers">
      <div class="winner-details">
        <div class="name">Patty T  Won</div>
        <div class="prize lg">$10,000</div>
        <div class="place"><span class="label">Where:</span> Seligman, AZ</div>
        <div class="date"><span class="label">When:</span> 02/27/2026</div>
        <div class="game"><span class="label">Playing:</span> Cash King</div>
      </div>
    </div>

No retailer info — we only get winner home city, which feeds pgeocode for the
centroid pin on the Big Wins map (same path as PA/AR/OK/LA).
"""
from __future__ import annotations
import concurrent.futures
import datetime as dt
import hashlib
import logging
import re

from backend.scraper.winners.base import WinnersScraper, is_draw_game

logger = logging.getLogger(__name__)

URL = "https://www.arizonalottery.com/winners/"
SOURCE_URL = URL

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class ArizonaWinnersScraper(WinnersScraper):
    state_code = "AZ"
    state_name = "Arizona"
    min_prize = 10000.0

    def scrape(self, days: int = 14) -> list[dict]:
        # `days` is ignored — AZ exposes its full multi-year list on one page,
        # and we de-dupe at upsert time anyway.
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            logger.warning("AZ winners: playwright not installed")
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._playwright_scrape).result()

    def _playwright_scrape(self) -> list[dict]:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=UA,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = ctx.new_page()
            try:
                page.goto(URL, wait_until="networkidle", timeout=60_000)
            except Exception as e:
                logger.warning("AZ winners goto error: %s", e)
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("div.card.winner")
        logger.info("AZ winners: %d cards on page", len(cards))

        out: list[dict] = []
        for card in cards:
            norm = self._parse_card(card)
            if norm:
                out.append(norm)
        return out

    def _parse_card(self, card) -> dict | None:
        # Filter by data-game first — AZ tags each card with the source game
        # category. Skip anything that isn't scratchers.
        data_game = (card.get("data-game") or "").lower()
        if data_game and data_game != "scratchers":
            return None

        name_el = card.select_one(".name")
        prize_el = card.select_one(".prize.lg") or card.select_one(".prize")
        place_el = card.select_one(".place")
        date_el = card.select_one(".date")
        game_el = card.select_one(".game")

        if not (prize_el and date_el and game_el):
            return None

        prize = _parse_prize(prize_el.get_text(" ", strip=True))
        if prize is None or prize < self.min_prize:
            return None

        claim_date = _parse_date(_strip_label(date_el.get_text(" ", strip=True)))

        game_name = _strip_label(game_el.get_text(" ", strip=True))
        if not game_name or game_name.upper() == "N/A":
            return None
        if is_draw_game(self.state_code, game_name):
            return None

        winner_city = None
        if place_el:
            raw_place = _strip_label(place_el.get_text(" ", strip=True))
            if raw_place and raw_place.upper() != "N/A":
                winner_city = _normalize_city(raw_place)

        winner_name = name_el.get_text(" ", strip=True) if name_el else ""
        winner_name = re.sub(r"\s+Won\s*$", "", winner_name, flags=re.IGNORECASE).strip()

        # AZ doesn't expose a stable per-win id. Build one from the fields that
        # uniquely identify a win in practice. We hash (name + date + prize +
        # city + game) so the value stays stable across re-scrapes — the page
        # only ever appends new winners to the front of the list.
        sid_seed = "|".join([
            winner_name or "",
            (claim_date.isoformat() if claim_date else ""),
            f"{int(prize)}",
            winner_city or "",
            game_name,
        ])
        source_id = hashlib.sha1(sid_seed.encode("utf-8")).hexdigest()[:24]

        return {
            "source_id": source_id,
            "source_game_id": None,
            "source_game_name": game_name,
            "prize_amount": prize,
            "claim_date": claim_date,
            "retailer_name": None,
            "retailer_address": None,
            "retailer_city": None,
            "retailer_zip": None,
            "winner_city": winner_city,
            "retailer_lat": None,
            "retailer_lng": None,
            "source_url": SOURCE_URL,
        }


# ── helpers ──────────────────────────────────────────────────────────────────

_LABEL_RE = re.compile(r"^(?:Where|When|Playing)\s*:\s*", re.IGNORECASE)


def _strip_label(text: str) -> str:
    return _LABEL_RE.sub("", text).strip()


def _parse_prize(text: str) -> float | None:
    """Handle '$10,000', '$1.5 Million', '$5M', '$10K' forms."""
    if not text:
        return None
    s = text.strip().lower().replace(",", "")
    m = re.search(r"\$\s*([\d.]+)\s*([mk])?(?:\s*(million|thousand))?", s)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    suffix = m.group(2) or ""
    word = m.group(3) or ""
    if suffix == "m" or word == "million":
        val *= 1_000_000
    elif suffix == "k" or word == "thousand":
        val *= 1_000
    return val


def _parse_date(text: str) -> dt.date | None:
    if not text:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _normalize_city(raw: str) -> str | None:
    """'Seligman, AZ' → 'Seligman, AZ'. Strip trailing punct and dedupe spaces."""
    raw = re.sub(r"\s+", " ", raw).strip().rstrip(",.")
    if not raw:
        return None
    # Ensure ", AZ" suffix for downstream pgeocode lookups.
    if "," not in raw:
        raw = f"{raw}, AZ"
    return raw
