"""
Oregon Lottery scratch-off scraper.

Second-chance: OR's site states 2nd Chance applies to all non-winning
Scratch-its broadly but only entries for the very last top prize per game
are recorded. There's no enumerated eligible-games list, and per the
project rule against blanket flags, has_second_chance stays FALSE for all
OR games.

Implementation note (2026-06-15): rewritten to drop Playwright entirely.
The site's earlier Mulesoft endpoint (osl-gameinfo-sys-api.cloudhub.io,
captured via header-sniff) was retired. The current setup:

- Listing HTML at /scratch-its/list/ inlines an `olapi` config object with
  scrambled `newClient`/`newSecret` and a `wp_scratchIts` array carrying
  per-game image URLs. We parse both directly with regex.
- Creds are descrambled by porting the site's `unscramble()` helper
  (reverse-Caesar shift-3, then reverse 4-char chunks).
- All game data comes from api.oregonlottery.org/gameinfo/v1/instant/games
  with the decoded client_id/client_secret headers.

No browser dependency = faster, no flake on credential-capture races, no
risk of being blocked by anti-bot on JS-rendered listing pages.
"""
from __future__ import annotations
import re
import json
import logging
from datetime import datetime, timezone

import requests
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://www.oregonlottery.org"
LISTING_URL = f"{BASE_URL}/scratch-its/list/"
# Prod API endpoint (Mulesoft proxy). The site's JS picks between this and
# a UAT host based on olapi.muleProd; we always want prod.
API_BASE = "https://api.oregonlottery.org/gameinfo/v1/instant/games"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class OregonScraper(BaseScraper):
    state_code = "OR"
    state_name = "Oregon"
    base_url = BASE_URL
    scraper_timeout = 300

    def scrape(self) -> list[dict]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
            "Referer": f"{BASE_URL}/",
        })

        creds, image_map = self._load_listing_config(session)
        session.headers["client_id"] = creds["client_id"]
        session.headers["client_secret"] = creds["client_secret"]

        listing = session.get(API_BASE, params={"count": 1000}, timeout=30)
        listing.raise_for_status()
        all_games = listing.json().get("InstantGames", []) or []

        active = [g for g in all_games if _is_active(g)]
        logger.info(
            "OR: API returned %d total games, %d active, %d images sniffed from listing",
            len(all_games), len(active), len(image_map),
        )

        games: list[dict] = []
        for meta in active:
            try:
                game = self._fetch_and_build(session, meta, image_map)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("OR: detail fetch failed for game %s: %s",
                             meta.get("GameNumber"), e)

        logger.info("OR: %d games scraped", len(games))
        return games

    def _load_listing_config(self, session: requests.Session) -> tuple[dict, dict[str, str]]:
        resp = session.get(LISTING_URL, headers={"User-Agent": UA}, timeout=30)
        resp.raise_for_status()
        html = resp.text

        m = re.search(r"var\s+olapi\s*=\s*(\{[^;]+\})\s*;", html)
        if not m:
            raise RuntimeError("OR: olapi config block not found on listing page")
        olapi = json.loads(m.group(1))
        scrambled_client = olapi.get("newClient")
        scrambled_secret = olapi.get("newSecret")
        if not scrambled_client or not scrambled_secret:
            raise RuntimeError("OR: olapi missing newClient/newSecret")
        creds = {
            "client_id": _unscramble(scrambled_client),
            "client_secret": _unscramble(scrambled_secret),
        }

        image_map: dict[str, str] = {}
        m2 = re.search(r"wp_scratchIts\s*=\s*(\[.*?\])\s*;", html, re.DOTALL)
        if m2:
            try:
                for entry in json.loads(m2.group(1)):
                    num = str(entry.get("number") or "").strip()
                    if not num:
                        continue
                    # image_preview[0] is the 300x300 resized variant; the WP
                    # naming convention puts -WxH right before the extension,
                    # so stripping that suffix yields the full-res original.
                    preview = entry.get("image_preview") or []
                    if preview and isinstance(preview, list):
                        url = preview[0]
                        full = re.sub(r"-\d+x\d+(\.[a-z]+)$", r"\1", url, flags=re.IGNORECASE)
                        image_map[num] = full
            except (ValueError, TypeError) as e:
                logger.warning("OR: wp_scratchIts parse failed: %s", e)

        return creds, image_map

    def _fetch_and_build(self, session: requests.Session, meta: dict, image_map: dict[str, str]) -> dict | None:
        game_num = str(meta.get("GameNumber") or "").strip()
        if not game_num:
            return None
        name = (meta.get("GameNameTitle") or "").strip()
        price = _to_float(meta.get("TicketPrice"))
        if not name or not price:
            return None

        resp = session.get(API_BASE, params={
            "gameNumber": game_num,
            "includePrizeTiers": "true",
        }, timeout=15)
        if resp.status_code != 200:
            logger.debug("OR: game %s api %s", game_num, resp.status_code)
            return None

        data = resp.json()
        detail = _first_dict(data, "InstantGames") or data
        if not isinstance(detail, dict):
            return None

        overall_odds = _to_float(detail.get("OverallOdds") or meta.get("OverallOdds"))

        tiers = []
        for t in (detail.get("PrizeTiers") or []):
            prize = _to_float(t.get("PrizeAmount"))
            total = _to_int(t.get("PrizesTotal"))
            remaining = _to_int(t.get("PrizesRemaining"))
            odds = _to_float(t.get("Odds"))
            if not prize or prize <= 0 or not total:
                continue
            tiers.append({
                "prize_amount": prize,
                "prizes_total": total,
                "prizes_remaining": remaining,
                # API "Odds" is probability per ticket; convert to "1 in N".
                "odds_one_in": (round(1 / odds, 2) if odds and odds > 0 else None),
            })

        if not tiers:
            return None

        total_printed = sum(t["prizes_total"] or 0 for t in tiers)
        total_remaining = sum(t["prizes_remaining"] or 0 for t in tiers)

        total_tickets = None
        tickets_remaining = None
        if overall_odds and total_printed > 0:
            total_tickets = round(overall_odds * total_printed)
            tickets_remaining = round(overall_odds * total_remaining)

        detail_url = f"{BASE_URL}/scratch-its/{_slugify(name)}/"
        image_url = image_map.get(game_num)
        if not image_url:
            image_url = _scrape_detail_image(session, detail_url, game_num)

        return self.build_game(
            game_id=game_num,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=detail_url,
            image_url=image_url,
        )


def _scrape_detail_image(session: requests.Session, detail_url: str, game_num: str) -> str | None:
    """Fallback: when wp_scratchIts has no entry for a game (rare — usually a
    new game whose WP post hasn't published yet), fetch its detail page and
    extract the /wp-content/uploads/{game_num}_*.jpg ticket image."""
    try:
        resp = session.get(detail_url, timeout=15)
        if resp.status_code != 200:
            return None
        pattern = re.compile(
            rf'/wp-content/uploads/[^"\'\s]*/{re.escape(game_num)}_[^"\'\s]*?\.(?:jpg|jpeg|png|webp)',
            re.IGNORECASE,
        )
        candidates: list[str] = []
        for m in pattern.finditer(resp.text):
            path = m.group(0)
            # Prefer the non-thumbnail variant (filename without -WxH suffix).
            if not re.search(r"-\d+x\d+\.[a-z]+$", path, re.IGNORECASE):
                return BASE_URL + path
            candidates.append(BASE_URL + path)
        return candidates[0] if candidates else None
    except Exception as e:
        logger.debug("OR: detail-image fallback failed for %s: %s", game_num, e)
        return None


def _unscramble(encoded: str, chunk_size: int = 4, shift: int = 3) -> str:
    """Port of the site's helpers.js `unscramble()`. Reverse-Caesar shift on
    ASCII letters, then reverse fixed-size chunks. Decodes olapi.newClient
    and olapi.newSecret into the real API credentials."""
    shift %= 26
    out = []
    for ch in encoded:
        code = ord(ch)
        if "a" <= ch <= "z":
            out.append(chr(((code - 97 - shift + 26) % 26) + 97))
        elif "A" <= ch <= "Z":
            out.append(chr(((code - 65 - shift + 26) % 26) + 65))
        else:
            out.append(ch)
    decoded = "".join(out)
    chunks = [decoded[i:i + chunk_size] for i in range(0, len(decoded), chunk_size)]
    chunks.reverse()
    return "".join(chunks)


def _is_active(g: dict) -> bool:
    """A game is for-sale if GameEndDate (sales end) is in the future or unset.
    ValidationEndDate is later — it's the claim deadline — and including those
    games would inflate the active list with dead-inventory titles."""
    end = g.get("GameEndDate")
    if not end:
        return True
    try:
        dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc)
    except Exception:
        return True


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _first_dict(payload, list_key: str):
    """Some endpoints wrap a single game in {InstantGames: [game]}, others return game dict."""
    if isinstance(payload, dict):
        if list_key in payload and isinstance(payload[list_key], list) and payload[list_key]:
            return payload[list_key][0]
    return None


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
