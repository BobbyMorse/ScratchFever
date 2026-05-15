"""
Kansas Lottery scratch-off scraper.
Data source: scratchodds.com/kansas/scratch-off-remaining-prizes
The page embeds all 61 KS games with full prize-tier data in an RSC
(React Server Component) payload as the `initialHomeScreenData` JSON blob.
kslottery.com/playonkansas.com only serves eInstants — no odds tables.
"""
from __future__ import annotations
import json
import logging
import re
from backend.scraper.base import BaseScraper

logger = logging.getLogger(__name__)

LISTING_URL = "https://scratchodds.com/kansas/scratch-off-remaining-prizes"


class KansasScraper(BaseScraper):
    state_code = "KS"
    state_name = "Kansas"
    base_url = "https://scratchodds.com"

    def scrape(self) -> list[dict]:
        games_data = self._fetch_games()
        games = []
        for g in games_data:
            try:
                game = self._build_from_scratchodds(g)
                if game:
                    games.append(game)
            except Exception as e:
                logger.debug("KS: failed to parse game %s: %s", g.get("gameName"), e)

        logger.info("KS: %d games scraped from scratchodds.com", len(games))
        return games

    def _fetch_games(self) -> list[dict]:
        resp = self.get(LISTING_URL)
        html = resp.text
        return _extract_games(html)

    def _build_from_scratchodds(self, g: dict) -> dict | None:
        name = g.get("gameName", "").strip()
        price = g.get("gamePrice")
        lottery_id = g.get("stateLotteryId", "")
        if not name or not price or not lottery_id:
            return None

        tiers_raw = g.get("remainingPrizesData", [])
        tiers = []
        tickets_remaining = None

        for t in tiers_raw:
            prize_amount = t.get("prizeAmount", 0)
            perc = t.get("percChanceOfWinning", 0)
            num_left = t.get("numLeft", 0)
            prize_type = t.get("prizeType", "cash")

            if prize_type == "free_ticket":
                continue
            if prize_amount <= 0 or perc <= 0:
                continue

            odds_one_in = 1.0 / perc
            if tickets_remaining is None and num_left > 0:
                tickets_remaining = int(round(num_left / perc))

            tiers.append({
                "prize_amount": float(prize_amount),
                "odds_one_in": odds_one_in,
                "prizes_remaining": int(num_left),
                "prizes_total": None,
            })

        stats = g.get("stats", {})
        overall_perc = stats.get("percChanceWinAnyPrize")
        overall_odds = (1.0 / overall_perc) if overall_perc else None

        return self.build_game(
            game_id=lottery_id,
            name=name,
            price=float(price),
            tiers=tiers,
            tickets_remaining=tickets_remaining,
            overall_odds=overall_odds,
            detail_url=g.get("gameLotteryUrl"),
            image_url=g.get("thumbnailGameImgLink"),
            end_date=None,
        )


def _extract_games(html: str) -> list[dict]:
    """Extract the games list from the initialHomeScreenData RSC blob."""
    idx = html.find("initialHomeScreenData")
    if idx == -1:
        raise ValueError("initialHomeScreenData not found in page")

    push_start = html.rfind('self.__next_f.push([1,"', 0, idx)
    if push_start == -1:
        raise ValueError("RSC push block not found")

    content_start = push_start + len('self.__next_f.push([1,"')
    raw = html[content_start:]

    # Walk the JS string to find the real closing quote (not backslash-escaped)
    backslash = False
    end_idx = None
    for i, ch in enumerate(raw):
        if backslash:
            backslash = False
            continue
        if ord(ch) == 92:  # backslash
            backslash = True
        elif ch == '"':
            end_idx = i
            break

    if end_idx is None:
        raise ValueError("Could not find end of RSC JS string")

    decoded = json.loads('"' + raw[:end_idx] + '"')

    ihd_start = decoded.find('"initialHomeScreenData":')
    obj_start = decoded.index("{", ihd_start)

    # Walk with proper string-aware brace matching
    depth = 0
    in_string = False
    esc = False
    end = None
    for i, ch in enumerate(decoded[obj_start:]):
        if esc:
            esc = False
            continue
        if in_string:
            if ord(ch) == 92:
                esc = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

    if end is None:
        raise ValueError("Could not find end of initialHomeScreenData object")

    data = json.loads(decoded[obj_start : obj_start + end])
    return data.get("games", [])
