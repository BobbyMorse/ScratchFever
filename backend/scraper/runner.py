"""
Runs all state scrapers and persists results to the database.

Concurrency model: scrapers run with bounded concurrency under two budgets —
a wider one for HTTP-based scrapers and a tighter one for Playwright scrapers
(each Chromium instance is RAM-heavy on Railway's 512MB dyno). Replaces the
prior sequential-with-180s-sleep loop.

Circuit breaker: a state that fails N times in a row gets skipped for an
exponentially-growing cooldown, so a broken site (e.g. IL Playwright timeout,
OH parser drift) no longer eats its full timeout slot every cycle.
"""
import asyncio
import logging
import time

from backend.database import get_pool, upsert_game, upsert_prize_tiers, log_scrape
from backend.scraper.playwright_base import PlaywrightScraper
from backend.scraper.states.texas import TexasScraper
from backend.scraper.states.florida import FloridaScraper
from backend.scraper.states.california import CaliforniaScraper
from backend.scraper.states.new_york import NewYorkScraper
from backend.scraper.states.pennsylvania import PennsylvaniaScraper
from backend.scraper.states.ohio import OhioScraper
from backend.scraper.states.michigan import MichiganScraper
from backend.scraper.states.illinois import IllinoisScraper
from backend.scraper.states.georgia import GeorgiaScraper
from backend.scraper.states.north_carolina import NorthCarolinaScraper
from backend.scraper.states.new_jersey import NewJerseyScraper
from backend.scraper.states.virginia import VirginiaScraper
from backend.scraper.states.massachusetts import MassachusettsScraper
from backend.scraper.states.maryland import MarylandScraper
from backend.scraper.states.colorado import ColoradoScraper
from backend.scraper.states.connecticut import ConnecticutScraper
from backend.scraper.states.vermont import VermontScraper
from backend.scraper.states.rhode_island import RhodeIslandScraper
from backend.scraper.states.new_hampshire import NewHampshireScraper
from backend.scraper.states.arizona import ArizonaScraper
from backend.scraper.states.washington import WashingtonScraper
from backend.scraper.states.oregon import OregonScraper
from backend.scraper.states.idaho import IdahoScraper
from backend.scraper.states.montana import MontanaScraper
from backend.scraper.states.wyoming import WyomingScraper
from backend.scraper.states.north_dakota import NorthDakotaScraper
from backend.scraper.states.south_dakota import SouthDakotaScraper
from backend.scraper.states.nebraska import NebraskaScraper
from backend.scraper.states.kansas import KansasScraper
from backend.scraper.states.minnesota import MinnesotaScraper
from backend.scraper.states.iowa import IowaScraper
from backend.scraper.states.wisconsin import WisconsinScraper
from backend.scraper.states.indiana import IndianaScraper
from backend.scraper.states.missouri import MissouriScraper
from backend.scraper.states.kentucky import KentuckyScraper
from backend.scraper.states.tennessee import TennesseeScraper
from backend.scraper.states.west_virginia import WestVirginiaScraper
from backend.scraper.states.south_carolina import SouthCarolinaScraper
from backend.scraper.states.arkansas import ArkansasScraper
from backend.scraper.states.louisiana import LouisianaScraper
from backend.scraper.states.oklahoma import OklahomaScraper
from backend.scraper.states.new_mexico import NewMexicoScraper
from backend.scraper.states.delaware import DelawareScraper
from backend.scraper.states.dc import DCScraper
from backend.scraper.states.mississippi import MississippiScraper
from backend.scraper.states.maine import MaineScraper

logger = logging.getLogger(__name__)

_cancel_requested = False

def request_cancel():
    global _cancel_requested
    _cancel_requested = True

def reset_cancel():
    global _cancel_requested
    _cancel_requested = False

ALL_SCRAPERS = [
    TexasScraper, FloridaScraper, CaliforniaScraper, NewYorkScraper, PennsylvaniaScraper,
    OhioScraper, MichiganScraper, IllinoisScraper, GeorgiaScraper, NorthCarolinaScraper,
    NewJerseyScraper, VirginiaScraper, MassachusettsScraper, MarylandScraper, ColoradoScraper,
    ConnecticutScraper, VermontScraper, RhodeIslandScraper, NewHampshireScraper,
    ArizonaScraper, WashingtonScraper, OregonScraper, IdahoScraper, MontanaScraper,
    WyomingScraper, NorthDakotaScraper, SouthDakotaScraper, NebraskaScraper, KansasScraper,
    MinnesotaScraper, IowaScraper, WisconsinScraper, IndianaScraper, MissouriScraper,
    KentuckyScraper, TennesseeScraper, WestVirginiaScraper, SouthCarolinaScraper,
    ArkansasScraper, LouisianaScraper, OklahomaScraper, NewMexicoScraper,
    DelawareScraper, DCScraper, MississippiScraper, MaineScraper,
]

DEFAULT_TIMEOUT = 120   # seconds for HTTP-based scrapers
PLAYWRIGHT_TIMEOUT = 600  # seconds for Playwright scrapers

# Concurrency budgets. Playwright is RAM-bound on 512MB Railway dyno — one at a
# time. HTTP scrapers can fan out more safely (asyncpg pool is 20).
HTTP_CONCURRENCY = 4
PLAYWRIGHT_CONCURRENCY = 1

# Circuit breaker. After this many consecutive failures, skip the state for a
# cooldown that doubles each subsequent failure (capped). In-memory only —
# resets on process restart, which is fine: Railway redeploys are infrequent
# and the persistent scrape_log table is the source of truth for history.
CB_FAIL_THRESHOLD = 3
CB_COOLDOWN_BASE = 30 * 60       # 30 min after first trip
CB_COOLDOWN_MAX = 24 * 60 * 60   # 24 hr ceiling

_cb_fail_count: dict[str, int] = {}
_cb_skip_until: dict[str, float] = {}


def _cb_should_skip(state_code: str) -> float | None:
    """Return seconds remaining in cooldown, or None to allow the scrape."""
    until = _cb_skip_until.get(state_code)
    if until and until > time.time():
        return until - time.time()
    return None


def _cb_record(state_code: str, success: bool) -> None:
    if success:
        _cb_fail_count.pop(state_code, None)
        _cb_skip_until.pop(state_code, None)
        return
    n = _cb_fail_count.get(state_code, 0) + 1
    _cb_fail_count[state_code] = n
    if n >= CB_FAIL_THRESHOLD:
        # n=3 → 30m, n=4 → 1h, n=5 → 2h, … capped at 24h
        cooldown = min(CB_COOLDOWN_BASE * (2 ** (n - CB_FAIL_THRESHOLD)), CB_COOLDOWN_MAX)
        _cb_skip_until[state_code] = time.time() + cooldown
        logger.warning(
            "%s: circuit breaker tripped (%d consecutive failures) — cooling down %d min",
            state_code, n, int(cooldown / 60),
        )


async def persist_games(conn, state_code: str, state_name: str, games: list[dict]):
    count = 0
    for game in games:
        try:
            tiers = game.pop("tiers", [])
            game_db_id = await upsert_game(conn, state_code, state_name, game["game_id"], game)
            if tiers:
                await upsert_prize_tiers(conn, game_db_id, tiers)
            count += 1
        except Exception as e:
            logger.error("DB persist error for %s/%s: %s", state_code, game.get("name"), e)
    return count


async def run_scraper(scraper_cls) -> tuple[str, int, str | None]:
    scraper = scraper_cls()
    if _cancel_requested:
        return scraper.state_code, 0, "cancelled"

    cooldown_left = _cb_should_skip(scraper.state_code)
    if cooldown_left is not None:
        msg = f"skipped: circuit breaker cooldown ({int(cooldown_left / 60)} min remaining)"
        logger.info("  %s: %s", scraper.state_code, msg)
        return scraper.state_code, 0, msg

    timeout = getattr(scraper, "scraper_timeout", DEFAULT_TIMEOUT)
    logger.info("Starting scraper: %s (%s)", scraper.state_name, scraper.state_code)
    try:
        games, error = await asyncio.wait_for(
            asyncio.to_thread(scraper.safe_scrape),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        error = f"timed out after {timeout}s"
        games = None
    count = 0
    if games and not _cancel_requested:
        try:
            async with get_pool().acquire() as conn:
                existing = await conn.fetchval(
                    "SELECT COUNT(*) FROM games WHERE state_code=$1 AND is_active=TRUE",
                    scraper.state_code
                )
                if existing and len(games) < existing * 0.5:
                    error = (
                        f"scraped {len(games)} games but DB has {existing} active — "
                        f"looks like a site outage, skipping update to protect existing data"
                    )
                    logger.warning("%s: %s", scraper.state_code, error)
                else:
                    async with conn.transaction():
                        await conn.execute("UPDATE games SET is_active=FALSE WHERE state_code=$1", scraper.state_code)
                        count = await persist_games(conn, scraper.state_code, scraper.state_name, games)
                        if count == 0:
                            raise RuntimeError(f"0/{len(games)} upserts succeeded — rolling back to protect existing data")
        except RuntimeError as e:
            error = error or str(e)
            logger.error("%s: %s", scraper.state_code, e)
    async with get_pool().acquire() as conn:
        await log_scrape(conn, scraper.state_code, error is None, count, error)
    # Don't count "site outage protection" as a circuit-breaker failure — the
    # scraper worked, the DB just refused the write. Only true scrape/persist
    # failures bump the counter.
    _cb_record(scraper.state_code, success=(error is None or "site outage" in (error or "")))
    logger.info("  %s: %d games%s", scraper.state_code, count, f" [ERROR: {error}]" if error else "")
    return scraper.state_code, count, error


async def run_all(state_filter: str = None, on_state=None) -> list[dict]:
    reset_cancel()
    scrapers = ALL_SCRAPERS
    if state_filter:
        sf = state_filter.upper()
        scrapers = [s for s in ALL_SCRAPERS if s.state_code.upper() == sf]
        # Manual single-state runs are explicit retries — bypass the breaker.
        _cb_fail_count.pop(sf, None)
        _cb_skip_until.pop(sf, None)

    active = [s for s in scrapers if not getattr(s, "disabled", False)]
    skipped = [s for s in scrapers if getattr(s, "disabled", False)]
    for s in skipped:
        logger.info("  %s: skipped (scraper marked disabled)", s.state_code)

    http_sem = asyncio.Semaphore(HTTP_CONCURRENCY)
    pw_sem = asyncio.Semaphore(PLAYWRIGHT_CONCURRENCY)

    async def _one(cls):
        if _cancel_requested:
            return {"state": cls.state_code, "games": 0, "error": "cancelled"}
        sem = pw_sem if issubclass(cls, PlaywrightScraper) else http_sem
        async with sem:
            if _cancel_requested:
                return {"state": cls.state_code, "games": 0, "error": "cancelled"}
            if on_state:
                on_state(cls.state_code, cls.state_name)
            try:
                code, count, error = await run_scraper(cls)
                return {"state": code, "games": count, "error": error}
            except Exception as exc:
                logger.error("Unhandled scraper exception in %s: %s", cls.state_code, exc)
                return {"state": cls.state_code, "games": 0, "error": str(exc)}

    results = await asyncio.gather(*[_one(cls) for cls in active])
    for s in skipped:
        results.append({"state": s.state_code, "games": 0, "error": "disabled"})
    return results


def run_all_sync(state_filter: str = None) -> list[dict]:
    return asyncio.run(run_all(state_filter))
