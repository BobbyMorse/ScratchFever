"""
State-bbox sanity guard for retailer lat/lon.

Source feeds occasionally publish coordinates that don't match the claimed
address — e.g. MA Lottery's own data put Nouria #1294 (Marlborough, MA) at
42.957, -71.504 (southern NH). One wrong pin slipped through; the guard is
to catch the next one before it goes live.

Usage in importers:
    from backend.geo_validate import validate_latlon
    lat, lon = validate_latlon("MA", raw_lat, raw_lon,
                               address=row["address"],
                               city=row["city"],
                               zip_code=row["zipCode"])

Behavior:
  - lat/lon in the state's rough bbox -> returned as-is
  - lat/lon out of bbox AND we have an address -> single-address Census
    geocode; if the new coords are in-bbox, use those
  - otherwise -> (None, None) so the row inserts without geo and gets
    picked up by backfill_retailer_geo.py, rather than pinning to the
    wrong spot on the map
"""
from __future__ import annotations
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Rough state bounding boxes (min_lat, max_lat, min_lon, max_lon) with a bit
# of slack at the borders. Purpose is to detect grossly wrong coords from
# upstream data feeds, not to enforce pixel-perfect state outlines.
STATE_BBOX: dict[str, tuple[float, float, float, float]] = {
    "AL": (30.10, 35.10, -88.55, -84.85),
    "AK": (51.00, 71.50, -179.99, -129.90),
    "AZ": (31.25, 37.05, -114.90, -108.95),
    "AR": (32.95, 36.55, -94.70, -89.55),
    "CA": (32.45, 42.05, -124.55, -114.05),
    "CO": (36.85, 41.05, -109.15, -101.95),
    "CT": (40.95, 42.10, -73.80, -71.70),
    "DE": (38.40, 39.90, -75.85, -74.95),
    "DC": (38.75, 39.05, -77.15, -76.85),
    "FL": (24.40, 31.05, -87.75, -79.85),
    "GA": (30.30, 35.05, -85.65, -80.70),
    "HI": (18.85, 22.30, -160.35, -154.75),
    "ID": (41.95, 49.05, -117.30, -110.95),
    "IL": (36.85, 42.65, -91.65, -87.35),
    "IN": (37.70, 41.85, -88.15, -84.65),
    "IA": (40.30, 43.65, -96.75, -90.05),
    "KS": (36.90, 40.10, -102.15, -94.50),
    "KY": (36.40, 39.20, -89.60, -81.90),
    "LA": (28.85, 33.10, -94.10, -88.70),
    "ME": (42.90, 47.55, -71.15, -66.85),
    "MD": (37.80, 39.80, -79.55, -74.95),
    "MA": (41.15, 42.92, -73.60, -69.85),
    "MI": (41.55, 48.45, -90.50, -82.25),
    "MN": (43.35, 49.45, -97.35, -89.35),
    "MS": (30.10, 35.05, -91.75, -88.05),
    "MO": (35.85, 40.70, -95.85, -88.95),
    "MT": (44.30, 49.10, -116.15, -103.85),
    "NE": (39.90, 43.10, -104.15, -95.15),
    "NV": (34.95, 42.10, -120.15, -113.95),
    "NH": (42.55, 45.40, -72.70, -70.45),
    "NJ": (38.80, 41.45, -75.65, -73.80),
    "NM": (31.20, 37.10, -109.15, -102.85),
    "NY": (40.40, 45.10, -79.85, -71.75),
    "NC": (33.70, 36.70, -84.45, -75.35),
    "ND": (45.75, 49.10, -104.15, -96.45),
    "OH": (38.25, 42.05, -84.95, -80.40),
    "OK": (33.45, 37.10, -103.15, -94.35),
    "OR": (41.85, 46.40, -124.75, -116.35),
    "PA": (39.55, 42.45, -80.65, -74.55),
    "RI": (41.05, 42.10, -71.95, -71.00),
    "SC": (31.95, 35.30, -83.45, -78.35),
    "SD": (42.35, 46.05, -104.15, -96.35),
    "TN": (34.85, 36.75, -90.45, -81.55),
    "TX": (25.65, 36.65, -106.75, -93.35),
    "UT": (36.85, 42.10, -114.15, -108.85),
    "VT": (42.55, 45.10, -73.55, -71.35),
    "VA": (36.45, 39.55, -83.75, -75.05),
    "WA": (45.45, 49.10, -124.95, -116.75),
    "WV": (37.05, 40.75, -82.75, -77.55),
    "WI": (42.35, 47.15, -92.95, -86.65),
    "WY": (40.85, 45.15, -111.15, -103.95),
}

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/address"


def in_state_bbox(state: Optional[str], lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    bb = STATE_BBOX.get((state or "").upper())
    if not bb:
        return True
    min_lat, max_lat, min_lon, max_lon = bb
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _census_single(address: str, city: str, state: str, zip_code: str) -> Optional[tuple[float, float]]:
    try:
        resp = requests.get(
            CENSUS_URL,
            params={
                "street": address or "",
                "city": city or "",
                "state": state or "",
                "zip": zip_code or "",
                "benchmark": "Public_AR_Current",
                "format": "json",
            },
            timeout=20,
        )
    except Exception as e:
        logger.warning("Census single-address geocode failed: %s", e)
        return None
    if resp.status_code != 200:
        return None
    try:
        matches = resp.json().get("result", {}).get("addressMatches") or []
    except ValueError:
        return None
    if not matches:
        return None
    coords = matches[0].get("coordinates") or {}
    lat, lon = coords.get("y"), coords.get("x")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def validate_latlon(
    state: Optional[str],
    lat: Optional[float],
    lon: Optional[float],
    address: Optional[str] = None,
    city: Optional[str] = None,
    zip_code: Optional[str] = None,
) -> tuple[Optional[float], Optional[float]]:
    """Return validated (lat, lon) — or geocoded replacements — for a retailer.

    Falls back to Census single-address geocoding when the supplied coords
    fall outside the claimed state's bbox. If Census also can't place it
    inside the state, returns (None, None) so the row goes in geo-less and
    the daily Census backfill can have another go later.
    """
    if in_state_bbox(state, lat, lon):
        return lat, lon

    if address:
        coords = _census_single(address, city or "", state or "", zip_code or "")
        if coords and in_state_bbox(state, coords[0], coords[1]):
            logger.warning(
                "geo_guard: re-geocoded out-of-bbox row state=%s addr=%r "
                "feed=(%s,%s) -> census=(%s,%s)",
                state, address, lat, lon, coords[0], coords[1],
            )
            return coords

    if lat is not None or lon is not None:
        logger.warning(
            "geo_guard: dropping out-of-bbox lat/lon state=%s addr=%r feed=(%s,%s)",
            state, address, lat, lon,
        )
    return None, None
