"""
Orchestrates per-state recent-winners scrapers and persists to reported_wins.
Runs independently of the games scraper — winners feeds are usually faster
and lighter, so this can run on a tighter schedule.
"""
from __future__ import annotations
import asyncio
import importlib
import logging

from backend.database import get_pool, upsert_reported_wins

logger = logging.getLogger(__name__)

# (module_suffix, class_name) — kept as data so a broken/missing module never
# nukes the whole registry. Bad ones get logged + skipped at import time.
# Without this guard, one missing scraper module 500s every endpoint that
# imports the runner (Data Status, Big Wins map, etc.).
_SCRAPER_SPECS: list[tuple[str, str]] = [
    ("massachusetts",  "MassachusettsWinnersScraper"),
    ("michigan",       "MichiganWinnersScraper"),
    ("rhode_island",   "RhodeIslandWinnersScraper"),
    ("pennsylvania",   "PennsylvaniaWinnersScraper"),
    ("georgia",        "GeorgiaWinnersScraper"),
    ("wisconsin",      "WisconsinWinnersScraper"),
    ("oklahoma",       "OklahomaWinnersScraper"),
    ("connecticut",    "ConnecticutWinnersScraper"),
    ("vermont",        "VermontWinnersScraper"),
    ("missouri",       "MissouriWinnersScraper"),
    ("arkansas",       "ArkansasWinnersScraper"),
    ("minnesota",      "MinnesotaWinnersScraper"),
    ("washington",     "WashingtonWinnersScraper"),
    ("louisiana",      "LouisianaWinnersScraper"),
    ("indiana",        "IndianaWinnersScraper"),
    ("delaware",       "DelawareWinnersScraper"),
    ("texas",          "TexasWinnersScraper"),
    ("new_mexico",     "NewMexicoWinnersScraper"),
    ("mississippi",    "MississippiWinnersScraper"),
    ("arizona",        "ArizonaWinnersScraper"),
    ("new_hampshire",  "NewHampshireWinnersScraper"),
    ("florida",        "FloridaWinnersScraper"),
    ("new_york",       "NewYorkWinnersScraper"),
    ("illinois",       "IllinoisWinnersScraper"),
    ("nebraska",       "NebraskaWinnersScraper"),
    ("iowa",           "IowaWinnersScraper"),
    ("south_dakota",   "SouthDakotaWinnersScraper"),
    ("montana",        "MontanaWinnersScraper"),
    ("kentucky",       "KentuckyWinnersScraper"),
    ("oregon",         "OregonWinnersScraper"),
]


def _load_scrapers() -> list[type]:
    loaded: list[type] = []
    for module_suffix, class_name in _SCRAPER_SPECS:
        module_path = f"backend.scraper.winners.{module_suffix}"
        try:
            mod = importlib.import_module(module_path)
            loaded.append(getattr(mod, class_name))
        except Exception as e:
            logger.error("Skipping winners scraper %s.%s: %s", module_path, class_name, e)
    return loaded


ALL_WINNERS_SCRAPERS = _load_scrapers()

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
