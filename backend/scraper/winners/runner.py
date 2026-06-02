"""
Orchestrates per-state recent-winners scrapers and persists to reported_wins.
Runs independently of the games scraper — winners feeds are usually faster
and lighter, so this can run on a tighter schedule.
"""
from __future__ import annotations
import asyncio
import logging

from backend.database import get_pool, upsert_reported_wins
from backend.scraper.winners.massachusetts import MassachusettsWinnersScraper
from backend.scraper.winners.michigan import MichiganWinnersScraper
from backend.scraper.winners.rhode_island import RhodeIslandWinnersScraper
from backend.scraper.winners.pennsylvania import PennsylvaniaWinnersScraper
from backend.scraper.winners.georgia import GeorgiaWinnersScraper
from backend.scraper.winners.wisconsin import WisconsinWinnersScraper
from backend.scraper.winners.oklahoma import OklahomaWinnersScraper
from backend.scraper.winners.connecticut import ConnecticutWinnersScraper
from backend.scraper.winners.vermont import VermontWinnersScraper
from backend.scraper.winners.missouri import MissouriWinnersScraper
from backend.scraper.winners.arkansas import ArkansasWinnersScraper
from backend.scraper.winners.minnesota import MinnesotaWinnersScraper
from backend.scraper.winners.washington import WashingtonWinnersScraper
from backend.scraper.winners.louisiana import LouisianaWinnersScraper
from backend.scraper.winners.indiana import IndianaWinnersScraper
from backend.scraper.winners.delaware import DelawareWinnersScraper
from backend.scraper.winners.texas import TexasWinnersScraper
from backend.scraper.winners.new_mexico import NewMexicoWinnersScraper
from backend.scraper.winners.mississippi import MississippiWinnersScraper
from backend.scraper.winners.arizona import ArizonaWinnersScraper
from backend.scraper.winners.new_hampshire import NewHampshireWinnersScraper
from backend.scraper.winners.florida import FloridaWinnersScraper
from backend.scraper.winners.new_york import NewYorkWinnersScraper
from backend.scraper.winners.illinois import IllinoisWinnersScraper
from backend.scraper.winners.nebraska import NebraskaWinnersScraper
from backend.scraper.winners.iowa import IowaWinnersScraper
from backend.scraper.winners.south_dakota import SouthDakotaWinnersScraper
from backend.scraper.winners.montana import MontanaWinnersScraper
from backend.scraper.winners.new_jersey import NewJerseyWinnersScraper
from backend.scraper.winners.kentucky import KentuckyWinnersScraper

logger = logging.getLogger(__name__)

ALL_WINNERS_SCRAPERS = [
    MassachusettsWinnersScraper,
    MichiganWinnersScraper,
    RhodeIslandWinnersScraper,
    PennsylvaniaWinnersScraper,
    GeorgiaWinnersScraper,
    WisconsinWinnersScraper,
    OklahomaWinnersScraper,
    ConnecticutWinnersScraper,
    VermontWinnersScraper,
    MissouriWinnersScraper,
    ArkansasWinnersScraper,
    MinnesotaWinnersScraper,
    WashingtonWinnersScraper,
    LouisianaWinnersScraper,
    IndianaWinnersScraper,
    DelawareWinnersScraper,
    TexasWinnersScraper,
    NewMexicoWinnersScraper,
    MississippiWinnersScraper,
    ArizonaWinnersScraper,
    NewHampshireWinnersScraper,
    FloridaWinnersScraper,
    NewYorkWinnersScraper,
    IllinoisWinnersScraper,
    NebraskaWinnersScraper,
    IowaWinnersScraper,
    SouthDakotaWinnersScraper,
    MontanaWinnersScraper,
    NewJerseyWinnersScraper,
    KentuckyWinnersScraper,
]

WINNERS_FEED_STATES = sorted({s.state_code for s in ALL_WINNERS_SCRAPERS})

TIMEOUT_SEC = 180


async def run_one(scraper_cls, days: int = 14) -> dict:
    scraper = scraper_cls()
    code = scraper.state_code
    try:
        wins, error = await asyncio.wait_for(
            asyncio.to_thread(scraper.safe_scrape, days),
            timeout=TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        return {"state": code, "saved": 0, "error": f"timed out after {TIMEOUT_SEC}s"}
    if error:
        return {"state": code, "saved": 0, "error": error}
    saved = 0
    try:
        async with get_pool().acquire() as conn:
            saved = await upsert_reported_wins(conn, code, wins)
    except Exception as e:
        logger.exception("%s winners upsert failed", code)
        return {"state": code, "saved": 0, "error": f"db: {e}"}
    return {"state": code, "saved": saved, "error": None}


async def run_all(state_filter: str | None = None, days: int = 14) -> list[dict]:
    scrapers = ALL_WINNERS_SCRAPERS
    if state_filter:
        scrapers = [s for s in scrapers if s.state_code.upper() == state_filter.upper()]
    results = []
    for cls in scrapers:
        results.append(await run_one(cls, days=days))
    return results


def run_all_sync(state_filter: str | None = None, days: int = 14) -> list[dict]:
    return asyncio.run(run_all(state_filter, days))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import sys
    state = sys.argv[1] if len(sys.argv) > 1 else None
    print(run_all_sync(state))
