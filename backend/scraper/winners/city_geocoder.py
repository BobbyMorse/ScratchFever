"""
City centroid geocoder.

For states whose winners feeds publish the WINNER'S HOME CITY but no retailer
address (e.g., PA, GA, FL), we map the win to that city's centroid. Centroid
is computed as the mean lat/lng across all USPS ZIPs in that city/state pair,
backed by the GeoNames postal database via pgeocode (~42k US ZIPs, cached on
first use under the user's home dir).

Returns None if the city can't be resolved. Cached in memory per-process.
"""
from __future__ import annotations
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_nomi = None
_nomi_lock = threading.Lock()
_cache: dict[tuple[str, str], Optional[tuple[float, float]]] = {}


def _get_nomi():
    global _nomi
    if _nomi is None:
        with _nomi_lock:
            if _nomi is None:
                import pgeocode  # lazy: heavy import (pandas)
                _nomi = pgeocode.Nominatim("US")
    return _nomi


def geocode_city(city: str, state_code: str) -> Optional[tuple[float, float]]:
    """
    Return (lat, lng) centroid for `city` in `state_code`, or None if not found.
    Repeated lookups for the same (city, state) are O(1) after the first hit.
    """
    if not city or not state_code:
        return None
    key = (city.strip().lower(), state_code.upper())
    if key in _cache:
        return _cache[key]

    try:
        nomi = _get_nomi()
        df = nomi.query_location(city.strip(), top_k=30)
        if df is None or df.empty:
            _cache[key] = None
            return None
        state_match = df[df["state_code"] == state_code.upper()]
        if state_match.empty:
            _cache[key] = None
            return None
        lat = float(state_match["latitude"].mean())
        lng = float(state_match["longitude"].mean())
        if not (lat == lat and lng == lng):  # NaN check
            _cache[key] = None
            return None
        result = (lat, lng)
        _cache[key] = result
        return result
    except Exception as e:
        logger.warning("city geocode failed for %s, %s: %s", city, state_code, e)
        _cache[key] = None
        return None


def geocode_many(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], tuple[float, float]]:
    """Geocode many (city, state) pairs at once, skipping misses. Returns hits only."""
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for city, state in pairs:
        hit = geocode_city(city, state)
        if hit is not None:
            out[(city.strip().lower(), state.upper())] = hit
    return out
