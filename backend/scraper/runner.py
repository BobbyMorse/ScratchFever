"""
Runs all state scrapers and persists results to the database.
"""
import asyncio
import logging
import aiosqlite

from backend.database import DB_PATH, upsert_game, upsert_prize_tiers, log_scrape
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
from backend.scraper.states.maine import MaineScraper
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

ALL_SCRAPERS = [
    TexasScraper,
    FloridaScraper,
    CaliforniaScraper,
    NewYorkScraper,
    PennsylvaniaScraper,
    OhioScraper,
    MichiganScraper,
    IllinoisScraper,
    GeorgiaScraper,
    NorthCarolinaScraper,
    NewJerseyScraper,
    VirginiaScraper,
    MassachusettsScraper,
    MarylandScraper,
    ColoradoScraper,
    ConnecticutScraper,
    VermontScraper,
    RhodeIslandScraper,
    MaineScraper,
    NewHampshireScraper,
    ArizonaScraper,
    WashingtonScraper,
    OregonScraper,
    IdahoScraper,
    MontanaScraper,
    WyomingScraper,
    NorthDakotaScraper,
    SouthDakotaScraper,
    NebraskaScraper,
    KansasScraper,
    MinnesotaScraper,
    IowaScraper,
    WisconsinScraper,
    IndianaScraper,
    MissouriScraper,
    KentuckyScraper,
    TennesseeScraper,
    WestVirginiaScraper,
    SouthCarolinaScraper,
    ArkansasScraper,
    LouisianaScraper,
    OklahomaScraper,
    NewMexicoScraper,
    DelawareScraper,
    DCScraper,
    MississippiScraper,
]


async def persist_games(db: aiosqlite.Connection, state_code: str, state_name: str, games: list[dict]):
    count = 0
    for game in games:
        try:
            tiers = game.pop("tiers", [])
            game_db_id = await upsert_game(db, state_code, state_name, game["game_id"], game)
            if tiers:
                await upsert_prize_tiers(db, game_db_id, tiers)
            count += 1
        except Exception as e:
            logger.error("DB persist error for %s/%s: %s", state_code, game.get("name"), e)
    await db.commit()
    return count


async def run_scraper(scraper_cls, db: aiosqlite.Connection):
    scraper = scraper_cls()
    logger.info("Starting scraper: %s (%s)", scraper.state_name, scraper.state_code)
    games, error = scraper.safe_scrape()
    count = 0
    if games:
        # Only deactivate existing games for this state when we have fresh data to replace them.
        # If the scrape fails, leave existing data active so states don't vanish.
        await db.execute("UPDATE games SET is_active=0 WHERE state_code=?", (scraper.state_code,))
        await db.commit()
        count = await persist_games(db, scraper.state_code, scraper.state_name, games)
    await log_scrape(db, scraper.state_code, error is None, count, error)
    await db.commit()
    return scraper.state_code, count, error


async def run_all(state_filter: str = None) -> list[dict]:
    results = []
    scrapers = ALL_SCRAPERS
    if state_filter:
        scrapers = [s for s in ALL_SCRAPERS if s.state_code.upper() == state_filter.upper()]

    async with aiosqlite.connect(DB_PATH) as db:
        for scraper_cls in scrapers:
            code, count, error = await run_scraper(scraper_cls, db)
            results.append({"state": code, "games": count, "error": error})
            logger.info("  %s: %d games%s", code, count, f" [ERROR: {error}]" if error else "")

    return results


def run_all_sync(state_filter: str = None) -> list[dict]:
    return asyncio.run(run_all(state_filter))
