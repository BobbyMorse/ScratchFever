"""
Shared helpers for state retailer scrapers.
"""
from __future__ import annotations
import hashlib
import logging
import time
import requests

from backend.geo_validate import validate_latlon

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
})


def make_external_id(*parts: str) -> str:
    """Hash arbitrary string parts into a stable external_id for states with no native ID."""
    combined = "|".join(p.strip().upper() for p in parts if p)
    return hashlib.md5(combined.encode()).hexdigest()


def safe_get(url: str, delay: float = 0.3, retries: int = 3, **kwargs) -> requests.Response | None:
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, timeout=30, **kwargs)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                logger.warning("GET %s rate-limited (429); backing off %ds", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            time.sleep(delay)
            return resp
        except Exception as e:
            logger.warning("GET %s attempt %d failed: %s", url, attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


async def upsert_retailers(conn, state_code: str, retailers: list[dict]) -> int:
    """Bulk upsert retailers into state_retailers table. Returns count inserted/updated."""
    if not retailers:
        return 0
    count = 0
    for r in retailers:
        await conn.execute("""
            INSERT INTO state_retailers
                (state_code, external_id, name, address, city, zip_code, phone, latitude, longitude, scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            ON CONFLICT (state_code, external_id) DO UPDATE SET
                name       = EXCLUDED.name,
                address    = EXCLUDED.address,
                city       = EXCLUDED.city,
                zip_code   = EXCLUDED.zip_code,
                phone      = EXCLUDED.phone,
                latitude   = EXCLUDED.latitude,
                longitude  = EXCLUDED.longitude,
                is_active  = TRUE,
                scraped_at = NOW()
        """,
            state_code,
            r["external_id"],
            r["name"],
            r.get("address"),
            r.get("city"),
            r.get("zip_code"),
            r.get("phone"),
            r.get("latitude"),
            r.get("longitude"),
        )
        count += 1
    return count
