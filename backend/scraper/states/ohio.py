"""
Ohio Lottery scratch-off scraper.

The api-solutions.ohiolottery.com JSON API is JWT-protected. The lottery's
public mobile bundle exposes a "mobilepublic" service account; once we hold
that JWT, two endpoints give us everything:

  POST /1.0/Authentication/Login          (authapi-solutions, json-patch+json)
    body: {"userName": "<svc>", "password": "<svc>"}
    → {"data": {"token": "<jwt>"}}

  GET  /1.0/Games/ScratchOffs/ScratchOffGame/GetAllGames
    → grouped by price bucket; per game: gameID, gameName, gameNumber,
      gamePrice, oddsOfWinning ("1 in 3.35"), gameGraphicScratchedURL,
      gameLogoURL, nodeAliasPath, closingDate.

  GET  /1.0/Games/ScratchOffs/ScratchOffGame/GetFullPrizesRemainingList
    → per game: gameId, prizeRemainingValues[{prizeValue, totalPrizes,
      prizesLeft}].

Previous implementation used Playwright to let the SPA fetch the JWT for us
and intercepted the responses. That approach silently captured nothing when
the auth fetch landed after networkidle fired, so the worker reported "ok"
with 0 games. Replicating the auth flow directly is faster (~2s vs ~45s) and
deterministic. Same credentials the retailer scraper uses.
"""
from __future__ import annotations
import logging
from urllib.parse import quote

import requests

from backend.scraper.base import BaseScraper
from backend.ev_calculator import parse_prize_amount, parse_odds

logger = logging.getLogger(__name__)

BASE_URL    = "https://www.ohiolottery.com"
AUTH_URL    = "https://authapi-solutions.ohiolottery.com/1.0/Authentication/Login"
PRIZES_URL  = "https://api-solutions.ohiolottery.com/1.0/Games/ScratchOffs/ScratchOffGame/GetFullPrizesRemainingList"
ALLGAMES_URL = "https://api-solutions.ohiolottery.com/1.0/Games/ScratchOffs/ScratchOffGame/GetAllGames"
USERNAME    = "mobilepublic@mtllc.com"
PASSWORD    = "R7V5Sz8@"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
}
_TIMEOUT = 60  # raised from 30 — Railway saw intermittent auth read-timeouts


class OhioScraper(BaseScraper):
    state_code = "OH"
    state_name = "Ohio"
    base_url = BASE_URL
    scraper_timeout = 180  # was 600 (Playwright); pure HTTP completes in seconds

    def scrape(self) -> list[dict]:
        sess = requests.Session()
        token = self._login(sess)
        if not token:
            return []

        meta_by_id = self._fetch_all_games(sess, token)
        prizes_by_id = self._fetch_prizes_remaining(sess, token)
        if not prizes_by_id:
            logger.warning("OH: prizes-remaining endpoint returned nothing")
            return []

        games: list[dict] = []
        for game_id, prize_entry in prizes_by_id.items():
            meta = meta_by_id.get(game_id)
            if not meta:
                # Game present in prizes-remaining but absent from GetAllGames —
                # usually a closing-out game with no marketing page. Skip rather
                # than emit it with no odds/image.
                continue
            built = self._build(meta, prize_entry)
            if built:
                games.append(built)

        logger.info("OH: %d games parsed", len(games))
        return games

    # ── HTTP plumbing ─────────────────────────────────────────────────────────

    def _login(self, sess: requests.Session) -> str | None:
        try:
            resp = sess.post(
                AUTH_URL,
                json={"userName": USERNAME, "password": PASSWORD},
                headers={**_HEADERS, "Content-Type": "application/json-patch+json"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.warning("OH: auth request error: %s", e)
            return None
        if resp.status_code != 200:
            logger.warning("OH: auth HTTP %d", resp.status_code)
            return None
        token = (resp.json().get("data") or {}).get("token")
        if not token:
            logger.warning("OH: auth response missing token")
        return token

    def _get_json(self, sess: requests.Session, url: str, token: str) -> dict | None:
        try:
            resp = sess.get(
                url,
                headers={**_HEADERS, "Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.warning("OH: GET %s error: %s", url, e)
            return None
        if resp.status_code != 200:
            logger.warning("OH: GET %s HTTP %d", url, resp.status_code)
            return None
        return resp.json()

    def _fetch_all_games(self, sess: requests.Session, token: str) -> dict[int, dict]:
        j = self._get_json(sess, ALLGAMES_URL, token) or {}
        buckets = (j.get("data") or {}) if isinstance(j.get("data"), dict) else {}
        out: dict[int, dict] = {}
        for _price, games in buckets.items():
            if not isinstance(games, list):
                continue
            for g in games:
                if isinstance(g, dict) and "gameID" in g:
                    out[g["gameID"]] = g
        logger.info("OH: %d games from GetAllGames", len(out))
        return out

    def _fetch_prizes_remaining(self, sess: requests.Session, token: str) -> dict[int, dict]:
        j = self._get_json(sess, PRIZES_URL, token) or {}
        rows = j.get("data") or []
        out = {r["gameId"]: r for r in rows if isinstance(r, dict) and "gameId" in r}
        logger.info("OH: %d games from GetFullPrizesRemainingList", len(out))
        return out

    # ── Per-game assembly ─────────────────────────────────────────────────────

    def _build(self, meta: dict, prize_entry: dict) -> dict | None:
        name = str(meta.get("gameName") or "").strip()
        if not name:
            return None

        game_number = str(meta.get("gameNumber") or meta.get("gameID") or "").strip()
        if not game_number:
            return None
        game_id = f"oh{game_number}"

        try:
            price = float(meta.get("gamePrice") or 0)
        except (TypeError, ValueError):
            price = 0
        if not price:
            return None

        overall_odds = parse_odds(str(meta.get("oddsOfWinning") or "")) or None

        tiers = self._parse_tiers(prize_entry.get("prizeRemainingValues") or [])
        if not tiers:
            return None

        total_tickets, tickets_remaining = self._estimate_tickets(tiers, overall_odds)

        image_path = meta.get("gameGraphicScratchedURL") or meta.get("gameLogoURL") or ""
        image_url = (BASE_URL + image_path) if image_path.startswith("/") else (image_path or None)

        node_path = meta.get("nodeAliasPath") or ""
        detail_url = (BASE_URL + quote(node_path, safe="/")) if node_path else f"{BASE_URL}/games/scratch-offs"

        end_date_raw = str(meta.get("closingDate") or "")[:10]
        # The API uses "0001-01-01" as the no-end-date sentinel.
        end_date = end_date_raw if end_date_raw and not end_date_raw.startswith("0001") else None

        return self.build_game(
            game_id=game_id,
            name=name,
            price=price,
            tiers=tiers,
            overall_odds=overall_odds,
            total_tickets=total_tickets,
            tickets_remaining=tickets_remaining,
            detail_url=detail_url,
            image_url=image_url,
            end_date=end_date,
        )

    @staticmethod
    def _parse_tiers(prize_rows: list) -> list[dict]:
        tiers = []
        for row in prize_rows or []:
            if not isinstance(row, dict):
                continue
            prize = parse_prize_amount(str(row.get("prizeValue") or 0))
            if not prize or prize <= 0:
                continue
            total = row.get("totalPrizes")
            remaining = row.get("prizesLeft")
            tiers.append({
                "prize_amount":     prize,
                "odds_one_in":      None,  # OH doesn't publish per-tier odds; derived below
                "prizes_total":     int(total) if isinstance(total, (int, float)) else None,
                "prizes_remaining": int(remaining) if isinstance(remaining, (int, float)) else 0,
            })
        return tiers

    @staticmethod
    def _estimate_tickets(
        tiers: list[dict], overall_odds: float | None
    ) -> tuple[int | None, int | None]:
        total_printed   = sum(t["prizes_total"] or 0 for t in tiers if t.get("prizes_total"))
        total_remaining = sum(t.get("prizes_remaining") or 0 for t in tiers)
        if overall_odds and total_printed > 0:
            return round(overall_odds * total_printed), round(overall_odds * total_remaining)
        return None, None
