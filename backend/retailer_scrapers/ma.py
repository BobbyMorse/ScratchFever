"""
Massachusetts retailer scraper — wraps retailer_scraper.scrape_and_save_db().
MA data goes into ma_retailers (its own table with keno/WoL metadata).
"""
from __future__ import annotations
import logging
import sys
import os

logger = logging.getLogger(__name__)


async def run(conn) -> int:
    # retailer_scraper.py lives at the project root; ensure it's importable
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from retailer_scraper import scrape_and_save_db
    from backend.ma_scorer import clear_cache

    result = await scrape_and_save_db(conn)
    clear_cache()
    logger.info("MA retailers: %s", result)
    return result.get("total", 0)
