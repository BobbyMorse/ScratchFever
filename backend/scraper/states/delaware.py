"""
Delaware Lottery scratch-off scraper.

Top Prizes page: https://www.delottery.com/Instant-Games/Top-Prizes-Remaining
  Links to a PDF: "Big Prizes Remaining" with columns:
    Game # | Game Name | $ AMT | Top Prize | Prizes Remaining | 2nd Top Tier Prize | Prizes Remaining

EV strategy:
  DE never publishes per-game odds in HTML/PDF form, but every game's
  marketing image (e.g. DE512OSv3.jpg) embeds a complete prize-tier table
  including ODDS of 1 IN, WINNERS (prizes_total), total tickets ordered,
  and overall odds. We OCR that image via Claude vision (see de_ocr.py),
  cache per image URL, and merge:
    - top + 2nd tier prizes_remaining from the PDF
    - all other tiers' odds + prizes_total from OCR
    - total_tickets from OCR
    - tickets_remaining estimated from top-tier remainder ratio
  EV is marked approximate because the sub-top tier remainders are
  scaled from the top-tier sell-through, not directly observed.
"""
import io
import logging
import re

import pdfplumber

from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount
from backend.scraper.states import de_ocr

logger = logging.getLogger(__name__)

TOP_PRIZES_URL = "https://www.delottery.com/Instant-Games/Top-Prizes-Remaining"
BASE_URL = "https://www.delottery.com"

# DE's Second-Chance page lists currently-eligible scratchers as
# <img alt="<NAME> instant game ticket">. We match by normalized name.
SECOND_CHANCE_URL = "https://www.delottery.com/Instant-Games/Second-Chance-Drawing"
_DE_SC_ALT_RE = re.compile(r'alt="([^"]+?)\s+instant\s+game\s+ticket"', re.I)


def _norm_de_name(s: str) -> str:
    s = s or ""
    s = s.replace("®", "").replace("™", "").replace("$", "").replace(",", "")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())


class DelawareScraper(BaseScraper):
    state_code = "DE"
    state_name = "Delaware"
    base_url = BASE_URL

    def scrape(self) -> list[dict]:
        entries = self._fetch_pdf_entries()
        if not entries:
            logger.warning("DE: no entries parsed from PDF")
            return []

        # Map game_number -> image_url from the Instant-Games landing page,
        # whose cards expose data-gamenumber + data-image attributes.
        image_map = self._fetch_image_map()
        logger.info("DE: %d image URLs from Instant-Games page", len(image_map))

        sc_names = self._fetch_second_chance_names()
        logger.info("DE second-chance eligible games: %d", len(sc_names))

        # OCR every game image up-front so we batch-share the cache and the
        # time budget across all DE games in this run.
        ocr_cache = de_ocr.load_cache()
        wanted_urls = [image_map.get(e["game_num"]) for e in entries]
        wanted_urls = [u for u in wanted_urls if u]
        ocr_results = de_ocr.ocr_batch(wanted_urls, cache=ocr_cache, session=self.session)
        logger.info(
            "DE OCR: %d/%d images resolved (cache + fresh)",
            len(ocr_results), len(wanted_urls),
        )

        games = []
        seen_ids: set[str] = set()
        ocr_hits = 0

        for e in entries:
            base_id = f"de{e['game_num']}"
            game_id = base_id
            suffix = ord('b')
            while game_id in seen_ids:
                game_id = base_id + chr(suffix)
                suffix += 1
            seen_ids.add(game_id)

            image_url = image_map.get(e["game_num"])
            ocr = ocr_results.get(image_url) if image_url else None

            tiers, tickets_remaining, total_tickets, overall_odds, ev_approx = (
                self._build_tiers(e, ocr)
            )
            if ocr and ocr.get("tiers"):
                ocr_hits += 1

            has_2c = _norm_de_name(e["name"]) in sc_names
            games.append(self.build_game(
                game_id=game_id,
                name=e["name"],
                price=e["price"],
                tiers=tiers,
                tickets_remaining=tickets_remaining,
                total_tickets=total_tickets,
                overall_odds=overall_odds,
                image_url=image_url,
                ev_approximate=ev_approx,
                has_second_chance=has_2c,
                second_chance_url=SECOND_CHANCE_URL if has_2c else None,
            ))

        logger.info("DE: %d games (%d with OCR-derived EV)", len(games), ocr_hits)
        return games

    def _build_tiers(self, entry: dict, ocr: dict | None):
        """Merge PDF top/2nd remainders with OCR full-tier data.

        Returns (tiers, tickets_remaining, total_tickets, overall_odds, ev_approximate).
        Falls back to PDF-only tiers (no EV) when OCR is missing or unusable."""
        # PDF tiers — always available, used as the fallback shape.
        pdf_tiers: list[dict] = []
        if entry["top_prize"] and entry["top_remaining"] is not None:
            pdf_tiers.append({
                "prize_amount": entry["top_prize"],
                "odds_one_in": None,
                "prizes_remaining": entry["top_remaining"],
                "prizes_total": None,
            })
        if entry["second_prize"] and entry["second_remaining"] is not None:
            pdf_tiers.append({
                "prize_amount": entry["second_prize"],
                "odds_one_in": None,
                "prizes_remaining": entry["second_remaining"],
                "prizes_total": None,
            })

        if not ocr or not ocr.get("tiers"):
            return pdf_tiers, None, None, None, False

        ocr_tiers = ocr["tiers"]
        total_tickets = ocr.get("total_tickets")
        overall_odds = ocr.get("overall_odds_one_in")

        # Pair PDF remainders to their OCR rows by largest-prize match.
        # OCR rows are sorted descending so the top OCR tier maps to PDF "top".
        ocr_tiers_sorted = sorted(
            ocr_tiers, key=lambda t: t["prize_amount"], reverse=True
        )

        pdf_remaining_by_amount: dict[float, int] = {}
        if entry["top_prize"] and entry["top_remaining"] is not None:
            pdf_remaining_by_amount[float(entry["top_prize"])] = entry["top_remaining"]
        if entry["second_prize"] and entry["second_remaining"] is not None:
            # If top == second prize, don't overwrite the larger remainder.
            pdf_remaining_by_amount.setdefault(
                float(entry["second_prize"]), entry["second_remaining"]
            )

        # Sell-through ratio from the top tier (the most reliable signal).
        sell_ratio = None  # remaining_fraction
        top_ocr = ocr_tiers_sorted[0] if ocr_tiers_sorted else None
        if (
            top_ocr
            and top_ocr.get("prizes_total")
            and top_ocr["prizes_total"] > 0
            and entry["top_remaining"] is not None
        ):
            ratio = entry["top_remaining"] / top_ocr["prizes_total"]
            # Clamp: a brand-new game can show >1 if PDF leads OCR; cap at 1.
            sell_ratio = max(0.0, min(1.0, ratio))

        tiers: list[dict] = []
        for ot in ocr_tiers_sorted:
            prize = ot["prize_amount"]
            odds = ot["odds_one_in"]
            total = ot.get("prizes_total")
            remaining = pdf_remaining_by_amount.get(float(prize))
            if remaining is None and total is not None and sell_ratio is not None:
                remaining = int(round(total * sell_ratio))
            tiers.append({
                "prize_amount": prize,
                "odds_one_in": odds,
                "prizes_total": total,
                "prizes_remaining": remaining,
            })

        # tickets_remaining estimated from total × sell_ratio. Only set if we
        # have both pieces — otherwise the EV calculator falls back to odds-only.
        tickets_remaining = None
        if total_tickets and sell_ratio is not None:
            tickets_remaining = int(round(total_tickets * sell_ratio))

        # EV is approximate whenever any tier's remainder was scaled rather
        # than directly observed (i.e. always, given DE only reports top + 2nd).
        return tiers, tickets_remaining, total_tickets, overall_odds, True

    def _fetch_second_chance_names(self) -> set[str]:
        """Pull normalized scratcher names currently on the DE 2nd-chance
        roster. Eligible games render as
        <img alt="<NAME> instant game ticket">. Empty set on failure."""
        try:
            resp = self.get(SECOND_CHANCE_URL, timeout=20)
        except Exception as e:
            logger.warning("DE second-chance fetch failed: %s", e)
            return set()
        names = set()
        for m in _DE_SC_ALT_RE.finditer(resp.text):
            n = _norm_de_name(m.group(1))
            if n:
                names.add(n)
        return names

    def _fetch_image_map(self) -> dict[str, str]:
        try:
            soup = self.soup("https://www.delottery.com/Instant-Games")
        except Exception as exc:
            logger.warning("DE: failed to fetch Instant-Games page: %s", exc)
            return {}

        out: dict[str, str] = {}
        for el in soup.find_all(attrs={"data-gamenumber": True}):
            gn = (el.get("data-gamenumber") or "").strip()
            img = (el.get("data-image") or "").strip()
            if not gn or not img:
                continue
            if img.startswith("/"):
                img = BASE_URL + img
            out.setdefault(gn, img)
        return out

    # ── PDF discovery + download ───────────────────────────────────────────────

    def _fetch_pdf_entries(self) -> list[dict]:
        try:
            soup = self.soup(TOP_PRIZES_URL)
        except Exception as exc:
            logger.warning("DE: failed to fetch top prizes page: %s", exc)
            return []

        pdf_href = None
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if re.search(r"big.prizes.remaining", h, re.I) or (
                h.lower().endswith(".pdf") and "instant" in h.lower()
            ):
                pdf_href = h
                break

        if not pdf_href:
            logger.warning("DE: no Big Prizes Remaining PDF link found")
            return []

        if pdf_href.startswith("/"):
            pdf_href = BASE_URL + pdf_href

        try:
            pdf_bytes = self.get(pdf_href).content
        except Exception as exc:
            logger.warning("DE: PDF download failed: %s", exc)
            return []

        return self._parse_pdf(pdf_bytes)

    # ── PDF parsing ───────────────────────────────────────────────────────────

    def _parse_pdf(self, pdf_bytes: bytes) -> list[dict]:
        text = ""
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    text += (page.extract_text() or "") + "\n"
        except Exception as exc:
            logger.warning("DE: pdfplumber error: %s", exc)
            return []

        entries = []
        for line in text.splitlines():
            line = line.strip()
            entry = self._parse_line(line)
            if entry:
                entries.append(entry)

        logger.info("DE: parsed %d entries from PDF", len(entries))
        return entries

    def _parse_line(self, line: str) -> dict | None:
        # Format: game_num [NEW] name [NEW] $price $top_prize top_rem $2nd_prize 2nd_rem
        # The "NEW" labels are marketing tags — strip them before matching.
        line = re.sub(r"\bNEW\b", "", line).strip()

        # Match: digits  name  $price  $top  int  $2nd  int
        m = re.match(
            r"^(\d+)\s+(.+?)\s+\$(\d+)\s+\$([\d,]+)\s+(\d+)\s+\$([\d,]+)\s+(\d+)\s*$",
            line,
        )
        if not m:
            return None

        game_num = m.group(1)
        raw_name = m.group(2).strip()
        price_str = m.group(3)
        top_prize_str = m.group(4)
        top_remaining = int(m.group(5))
        second_prize_str = m.group(6)
        second_remaining = int(m.group(7))

        # Clean name: remove stray "NEW" fragments and normalize whitespace
        name = re.sub(r"\s+", " ", raw_name).strip()
        # Remove surrounding quotes that appear on some game names e.g. '"SCRABBLE"'
        name = name.strip('"').strip("'").strip()
        if not name:
            return None

        price = float(price_str)
        top_prize = float(top_prize_str.replace(",", ""))
        second_prize = float(second_prize_str.replace(",", ""))

        # Sanity: price must be a known ticket price; top_prize > 0
        if price not in {1, 2, 3, 5, 10, 20, 25, 30, 50} or top_prize <= 0:
            return None

        return {
            "game_num": game_num,
            "name": name,
            "price": price,
            "top_prize": top_prize,
            "top_remaining": top_remaining,
            "second_prize": second_prize,
            "second_remaining": second_remaining,
        }
