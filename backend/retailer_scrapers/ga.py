"""
Georgia retailer scraper.
API: galottery.com/api/v1/locations?city=CITY (IGT platform)
Only supports city-name queries; iterate through all GA cities.
~1 request per city, ~400 cities, deduplicates by retailer ID.
"""
from __future__ import annotations
import logging
from .base import safe_get, upsert_retailers

logger = logging.getLogger(__name__)

API_URL = "https://www.galottery.com/api/v1/locations"

# All incorporated places + unincorporated communities in GA that appear in retailer data.
# County seats are guaranteed; major cities and suburbs added for coverage.
GA_CITIES = [
    "Abbeville", "Acworth", "Adairsville", "Adel", "Ailey", "Alamo", "Albany",
    "Alma", "Alpharetta", "Alston", "Alto", "Americus", "Appling", "Arabi",
    "Arlington", "Arnoldsville", "Athens", "Atlanta", "Attapulgus", "Auburn",
    "Augusta", "Austell", "Axson", "Bainbridge", "Ball Ground", "Barnesville",
    "Baxley", "Bellville", "Blackshear", "Blairsville", "Blakely", "Blue Ridge",
    "Bogart", "Bowdon", "Braselton", "Bremen", "Brookhaven", "Brooklet",
    "Brunswick", "Buchanan", "Buena Vista", "Buford", "Butler", "Byron",
    "Cairo", "Calhoun", "Camilla", "Canton", "Carnesville", "Carrollton",
    "Cartersville", "Cave Spring", "Cedartown", "Chatsworth", "Clarkesville",
    "Clarkston", "Claxton", "Cleveland", "Cochran", "College Park", "Collins",
    "Colquitt", "Columbus", "Conyers", "Cordele", "Cornelia", "Covington",
    "Cumming", "Cuthbert", "Dacula", "Dahlonega", "Dallas", "Dalton",
    "Danielsville", "Darien", "Dawson", "Dawsonville", "Decatur", "Demorest",
    "Donalsonville", "Douglas", "Douglasville", "Dublin", "Duluth", "Dunwoody",
    "East Dublin", "East Point", "Eastman", "Eatonton", "Edison", "Elberton",
    "Ellaville", "Ellijay", "Enigma", "Eton", "Euharlee", "Evans",
    "Experiment", "Fairburn", "Fayetteville", "Fitzgerald", "Folkston",
    "Forest Park", "Forsyth", "Fort Oglethorpe", "Fort Valley", "Gainesville",
    "Georgetown", "Gibson", "Glenwood", "Glennville", "Grayson", "Greensboro",
    "Greenville", "Griffin", "Grovetown", "Guyton", "Hampton", "Hapeville",
    "Harlem", "Hartwell", "Hawkinsville", "Hazlehurst", "Hephzibah",
    "Hiawassee", "Hinesville", "Hogansville", "Holly Springs", "Homerville",
    "Irwinton", "Jackson", "Jasper", "Jefferson", "Jeffersonville", "Jesup",
    "Johns Creek", "Jonesboro", "Kennesaw", "Kingsland", "Kingston",
    "La Fayette", "LaFayette", "LaGrange", "Lake City", "Lakeland",
    "Lavonia", "Lawrenceville", "Leesburg", "Lexington", "Lincolnton",
    "Lithia Springs", "Lithonia", "Locust Grove", "Loganville", "Louisville",
    "Lula", "Lumpkin", "Lyons", "Mableton", "Macon", "Madison", "Manchester",
    "Marietta", "McDonough", "McRae", "McRae-Helena", "Meansville",
    "Metter", "Midway", "Milledgeville", "Millen", "Milton", "Monroe",
    "Montezuma", "Monticello", "Morrow", "Moultrie", "Mount Airy",
    "Mount Vernon", "Mount Zion", "Nashville", "Newnan", "Newton", "Norcross",
    "Norman Park", "Oakwood", "Ocilla", "Oglethorpe", "Omega", "Oxford",
    "Palmetto", "Patterson", "Peachtree City", "Pelham", "Perry", "Plains",
    "Pooler", "Port Wentworth", "Powder Springs", "Preston", "Quitman",
    "Ranger", "Red Oak", "Reidsville", "Riceboro", "Rincon", "Ringgold",
    "Riverdale", "Rockmart", "Rome", "Rossville", "Roswell", "Royston",
    "Rutledge", "Sandy Springs", "Sandersville", "Sardis", "Savannah",
    "Shellman", "Smyrna", "Snellville", "Social Circle", "Sparta",
    "Springfield", "Statesboro", "Statenville", "Statham", "Stockbridge",
    "Stone Mountain", "Stonecrest", "Sugar Hill", "Sugar Valley", "Summerville",
    "Suwanee", "Swainsboro", "Sylvania", "Sylvester", "Tallapoosa",
    "Temple", "Tennille", "Thomaston", "Thomasville", "Thomson", "Tifton",
    "Toccoa", "Trenton", "Tucker", "Twin City", "Tyrone", "Unadilla",
    "Union City", "Valdosta", "Vidalia", "Vienna", "Villa Rica", "Wadley",
    "Warner Robins", "Warrenton", "Washington", "Watkinsville", "Waycross",
    "Waynesboro", "Winder", "Winterville", "Woodbine", "Woodstock",
    "Wrens", "Wrightsville", "Young Harris", "Zebulon",
]


def _parse_retailer(item: dict) -> dict | None:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    external_id = str(item.get("id") or "").strip()
    if not external_id:
        return None
    lat = item.get("lattitude") or item.get("latitude")
    lng = item.get("longitude")
    try:
        lat = float(lat) if lat is not None else None
        lng = float(lng) if lng is not None else None
    except (ValueError, TypeError):
        lat, lng = None, None
    phone = (item.get("phoneNumber") or "").strip() or None
    if phone in ("9999999999", "0000000000"):
        phone = None
    return {
        "external_id": external_id,
        "name": name,
        "address": (item.get("address1") or "").strip() or None,
        "city": (item.get("city") or "").strip() or None,
        "zip_code": (item.get("postalCode") or "").strip() or None,
        "phone": phone,
        "latitude": lat,
        "longitude": lng,
    }


def scrape_ga() -> list[dict]:
    retailers: list[dict] = []
    seen_ids: set[str] = set()

    for city in GA_CITIES:
        resp = safe_get(API_URL, params={"status": "Active", "city": city, "size": 200}, delay=0.3)
        if resp is None:
            logger.warning("GA: failed to fetch city=%s", city)
            continue
        try:
            data = resp.json()
        except Exception:
            continue
        items = data.get("locations") or []
        new_count = 0
        for item in items:
            r = _parse_retailer(item)
            if r and r["external_id"] not in seen_ids:
                seen_ids.add(r["external_id"])
                retailers.append(r)
                new_count += 1
        if new_count:
            logger.info("GA city=%s: %d new retailers (total: %d)", city, new_count, len(retailers))

    logger.info("GA: scraped %d unique retailers", len(retailers))
    return retailers


async def run(conn) -> int:
    retailers = scrape_ga()
    return await upsert_retailers(conn, "GA", retailers)
