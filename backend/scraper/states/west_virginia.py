"""
West Virginia Lottery scratch-off / instant-game scraper.

wvlottery.com is a Next.js app. Despite the SPA shell, each detail page's
SSR HTML embeds the full game payload (price, total tickets, overall odds,
per-tier prizeDetails, image URL) as an escaped JSON blob inside the React
RSC stream (the `__next_f.push` chunks Next.js uses to ship data to the
client).

Strategy:
  1. Fetch sitemap.xml (static, no JS) → list of game URLs + numeric ids.
  2. Fetch each detail page over plain HTTP and regex out the embedded JSON
     fields. The Playwright DOM-text parser in the previous implementation
     looked for English labels ("Overall Odds\\nX.XX") that only appear after
     React mounts, so the inner_text scrape consistently produced empty
     tier tables.

Field layout in the SSR HTML (escaped JSON, so backslash-quoted keys):
  \"gameNumber\":\"1260\"
  \"title\":\"TACO - BREW MATCH\"
  \"ticketPrice\":5
  \"totalTickets\":720000
  \"odds\":\"4.31\"
  \"prizeDetails\":[{\"prize\":5,\"totalPrizes\":75441,\"remainingPrizes\":60394}, ...]
  \"image\":{\"url\":\"https://images.ctfassets.net/...\"}
"""
from __future__ import annotations
import json
import logging
import re

from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://wvlottery.com/sitemap.xml"
BASE_URL    = "https://www.wvlottery.com"

# Sitemap entry like: /games/scratch-offs/1260-taco-brew-match
SITEMAP_GAME_RE = re.compile(
    r"<loc>(https://wvlottery\.com/games/scratch-offs/(\d+)-[^<]+)</loc>"
)

# Regexes against the doubly-escaped JSON inside __next_f.push payloads.
# Each \" appears as \\" in the source HTML. We match against the raw text.
_RE_TITLE         = re.compile(r'\\"title\\":\\"([^"\\]+)')
_RE_PRICE         = re.compile(r'\\"ticketPrice\\":(\d+(?:\.\d+)?)')
_RE_TOTAL_TICKETS = re.compile(r'\\"totalTickets\\":(\d+)')
_RE_ODDS          = re.compile(r'\\"odds\\":\\"([\d.]+)\\"')
_RE_IMAGE         = re.compile(r'\\"image\\":\{\\"url\\":\\"([^"\\]+)')
_RE_PRIZE_TIER    = re.compile(
    r'\\"prize\\":(\d+(?:\.\d+)?),'
    r'\\"totalPrizes\\":(\d+),'
    r'\\"remainingPrizes\\":(\d+)'
)


class WestVirginiaScraper(BaseScraper):
    state_code = "WV"
    state_name = "West Virginia"
    base_url = BASE_URL
    scraper_timeout = 180  # was 900 with Playwright; pure HTTP completes in seconds

    def scrape(self) -> list[dict]:
        try:
            sitemap = self.get(SITEMAP_URL).text
        except Exception as e:
            logger.warning("WV: sitemap fetch failed: %s", e)
            return []

        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in SITEMAP_GAME_RE.finditer(sitemap):
            url, game_num = m.group(1), m.group(2)
            if game_num in seen:
                continue
            seen.add(game_num)
            entries.append((url, game_num))
        logger.info("WV: %d game URLs from sitemap", len(entries))

        games: list[dict] = []
        for url, game_num in entries:
            try:
                game = self._scrape_detail(url, game_num)
            except Exception as e:
                logger.debug("WV detail error %s: %s", url, e)
                continue
            if game:
                games.append(game)

        logger.info("WV: %d games scraped", len(games))
        return games

    def _scrape_detail(self, url: str, game_num: str) -> dict | None:
        try:
            html = self.get(url).text
        except Exception as e:
            logger.debug("WV: GET %s failed: %s", url, e)
            return None

        title = _first(_RE_TITLE, html)
        if not title:
            return None
        # JSON-decoded titles can contain escape sequences; cheap unescape.
        name = title.replace('\\u0026', '&').replace('\\\\', '\\').strip()

        price = _first_float(_RE_PRICE, html)
        if not price:
            return None

        total_tickets = _first_int(_RE_TOTAL_TICKETS, html)
        odds_str = _first(_RE_ODDS, html)
        overall_odds = float(odds_str) if odds_str else None

        image_url = _first(_RE_IMAGE, html)
        if image_url:
            image_url = image_url.replace('\\/', '/').strip()

        tiers: list[dict] = []
        seen_prize: set[float] = set()
        for tm in _RE_PRIZE_TIER.finditer(html):
            prize = float(tm.group(1))
            if prize <= 0 or prize in seen_prize:
                continue
            seen_prize.add(prize)
            total = int(tm.group(2))
            remaining = int(tm.group(3))
            tiers.append({
                "prize_amount":     prize,
                # WV doesn't publish per-tier odds; derive from total_tickets/total
                "odds_one_in":      (total_tickets / total) if (total_tickets and total > 0) else None,
                "prizes_total":     total,
                "prizes_remaining": remaining,
            })
        if not tiers:
            return None

        tickets_remaining = (
            round(overall_odds * sum(t["prizes_remaining"] for t in tiers))
            if overall_odds else None
        )

        return self.build_game(
            game_id=f"wv{game_num}",
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=url,
            image_url=image_url,
        )


def _first(rx: re.Pattern[str], text: str) -> str | None:
    m = rx.search(text)
    return m.group(1) if m else None


def _first_float(rx: re.Pattern[str], text: str) -> float | None:
    v = _first(rx, text)
    return float(v) if v else None


def _first_int(rx: re.Pattern[str], text: str) -> int | None:
    v = _first(rx, text)
    return int(v) if v else None
