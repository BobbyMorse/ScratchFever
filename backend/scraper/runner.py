"""
Runs all state scrapers and persists results to the database.
"""
import asyncio
import logging

from backend.database import get_pool, upsert_game, upsert_prize_tiers, log_scrape
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
    DelawareScraper, DCScraper, MississippiScraper,
]

# Max scrapers running simultaneously. Each scraper makes HTTP requests to an
# external site; too many in parallel risks getting IP-blocked.
CONCURRENCY = 10


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


DEFAULT_TIMEOUT = 120   # seconds for HTTP-based scrapers
PLAYWRIGHT_TIMEOUT = 600  # seconds for Playwright scrapers (up to 74 pages)


async def run_scraper(scraper_cls, sem: asyncio.Semaphore) -> tuple[str, int, str | None]:
    async with sem:
        if _cancel_requested:
            scraper = scraper_cls()
            return scraper.state_code, 0, "cancelled"
        scraper = scraper_cls()
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
            async with get_pool().acquire() as conn:
                await conn.execute("UPDATE games SET is_active=FALSE WHERE state_code=$1", scraper.state_code)
                count = await persist_games(conn, scraper.state_code, scraper.state_name, games)
        async with get_pool().acquire() as conn:
            await log_scrape(conn, scraper.state_code, error is None, count, error)
        logger.info("  %s: %d games%s", scraper.state_code, count, f" [ERROR: {error}]" if error else "")
        return scraper.state_code, count, error


async def run_all(state_filter: str = None) -> list[dict]:
    reset_cancel()
    scrapers = ALL_SCRAPERS
    if state_filter:
        scrapers = [s for s in ALL_SCRAPERS if s.state_code.upper() == state_filter.upper()]

    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [run_scraper(cls, sem) for cls in scrapers]
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for item in results_raw:
        if isinstance(item, Exception):
            logger.error("Unhandled scraper exception: %s", item)
            results.append({"state": "?", "games": 0, "error": str(item)})
        else:
            code, count, error = item
            results.append({"state": code, "games": count, "error": error})
    return results


def run_all_sync(state_filter: str = None) -> list[dict]:
    return asyncio.run(run_all(state_filter))
