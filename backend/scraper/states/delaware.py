"""
Delaware Lottery scratch-off scraper.

Top Prizes page: https://www.delottery.com/Instant-Games/Top-Prizes-Remaining
  Links to a PDF: "Big Prizes Remaining" with columns:
    Game # | Game Name | $ AMT | Top Prize | Prizes Remaining | 2nd Top Tier Prize | Prizes Remaining

Delaware does not publish per-game odds tables, so EV remains NULL.
We populate top_prize and top_prize_remaining from the PDF so those fields
appear on the site even without EV data.
"""
import io
import logging
import re

import pdfplumber

from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount

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

        games = []
        seen_ids: set[str] = set()

        for e in entries:
            base_id = f"de{e['game_num']}"
            game_id = base_id
            suffix = ord('b')
            while game_id in seen_ids:
                game_id = base_id + chr(suffix)
                suffix += 1
            seen_ids.add(game_id)

            tiers = []
            if e["top_prize"] and e["top_remaining"] is not None:
                tiers.append({
                    "prize_amount": e["top_prize"],
                    "odds_one_in": None,
                    "prizes_remaining": e["top_remaining"],
                    "prizes_total": None,
                })
            if e["second_prize"] and e["second_remaining"] is not None:
                tiers.append({
                    "prize_amount": e["second_prize"],
                    "odds_one_in": None,
                    "prizes_remaining": e["second_remaining"],
                    "prizes_total": None,
                })

            games.append(self.build_game(
                game_id=game_id,
                name=e["name"],
                price=e["price"],
                tiers=tiers,
                image_url=image_map.get(e["game_num"]),
            ))

        logger.info("DE: %d games from PDF", len(games))
        return games

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
