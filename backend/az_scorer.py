"""
AZ retailer scoring logic — shared by the API.
"""

import csv
import os

_DIR = os.path.dirname(__file__)
AZ_CSV_ENRICHED = os.path.join(_DIR, "..", "az_retailers_enriched.csv")
AZ_CSV_BASE     = os.path.join(_DIR, "..", "az_retailers.csv")

CHAIN_KEYWORDS = [
    "7-ELEVEN", "7 ELEVEN", "CIRCLE K", "QUIKTRIP", "QT ",
    "FRY'S FOOD", "FRYS FOOD", "FRIES FOOD", "SAFEWAY", "BASHA",
    "BASHAS", "WALGREENS", "CVS", "WALMART", "WAL-MART", "TARGET",
    "COSTCO", "DOLLAR GENERAL", "DOLLAR TREE", "FAMILY DOLLAR",
    "RITE AID", "SHELL ", "BP ", "CHEVRON", "MOBIL", "EXXON", "SUNOCO",
    "TOTAL WINE", "BEVMO", "ALBERTSONS", "SPROUTS", "WHOLE FOODS",
    "TRADER JOE", "FOOD CITY", "WINCO", "SMITH'S FOOD", "SMITHS FOOD",
    "ARCO ", "CIRCLE K", "QUICKTRIP", "PILOT ", "FLYING J",
    "LOVES ", "LOVE'S ", "CASEY'S", "SPEEDWAY", "MARATHON",
    "SEARS", "KMART", "MEIJER", "PUBLIX", "KROGER",
    "CIRCLE K", "WINN DIXIE",
]

# Phoenix & Tucson metro — high ticket volume/churn
METRO_CITIES = {
    "PHOENIX", "SCOTTSDALE", "TEMPE", "CHANDLER", "GILBERT", "MESA",
    "GLENDALE", "PEORIA", "SURPRISE", "AVONDALE", "GOODYEAR", "BUCKEYE",
    "QUEEN CREEK", "SAN TAN VALLEY", "MARICOPA", "FOUNTAIN HILLS",
    "PARADISE VALLEY", "CAVE CREEK", "CAREFREE", "LITCHFIELD PARK",
    "TOLLESON", "LAVEEN", "EL MIRAGE", "YOUNGTOWN", "SUN CITY",
    "SUN CITY WEST", "ANTHEM", "WADDELL",
    "TUCSON", "ORO VALLEY", "MARANA", "SAHUARITA", "SOUTH TUCSON",
    "CATALINA FOOTHILLS", "TANQUE VERDE",
    "YUMA", "FLAGSTAFF", "LAKE HAVASU CITY", "KINGMAN",
}

# Border / high-transient cities
BORDER_CITIES = {
    "NOGALES", "DOUGLAS", "SAN LUIS", "SOMERTON", "WELLTON",
    "LUKEVILLE", "NACO", "BISBEE", "AGUA PRIETA",
}

# Mid-size cities — moderate score
TIER_A_CITIES = {
    "PRESCOTT", "PRESCOTT VALLEY", "CHINO VALLEY", "DEWEY", "HUMBOLDT",
    "COTTONWOOD", "SEDONA", "CAMP VERDE", "CLARKDALE",
    "SIERRA VISTA", "BENSON", "WILLCOX",
    "BULLHEAD CITY", "FORT MOHAVE", "LAUGHLIN",
    "CASA GRANDE", "COOLIDGE", "ELOY", "FLORENCE",
    "APACHE JUNCTION", "GOLD CANYON",
    "SUN LAKES", "CHANDLER HEIGHTS",
    "PAYSON", "PINE", "STRAWBERRY",
    "SAFFORD", "THATCHER", "STAFFORD",
    "GLOBE", "MIAMI",
    "WICKENBURG", "BUCKEYE",
    "NEW RIVER", "ANTHEM",
}

# Rural / remote AZ — lower ticket volume (higher score)
RURAL_CITIES = {
    "HOLBROOK", "WINSLOW", "SNOWFLAKE", "TAYLOR", "SHOW LOW",
    "PINETOP", "LAKESIDE", "PINETOP-LAKESIDE",
    "EAGAR", "SPRINGERVILLE", "ST. JOHNS", "SAINT JOHNS",
    "WILLIAMS", "ASH FORK", "SELIGMAN", "PEACH SPRINGS",
    "PARKER", "QUARTZSITE", "SALOME", "HOPE",
    "BAGDAD", "CHLORIDE", "OATMAN",
    "CHINLE", "WINDOW ROCK", "FORT DEFIANCE",
    "TUBA CITY", "KAYENTA",
    "WHITERIVER", "CIBECUE",
    "ELGIN", "SONOITA", "PATAGONIA",
    "TOMBSTONE",
    "DUNCAN", "CLIFTON", "MORENCI",
    "HAYDEN", "WINKELMAN",
    "MIAMI", "GLOBE",
}

INDIE_KEYWORDS = [
    "VARIETY", "LIQUOR", "PACKAGE", "SMOKE", "CIGAR", "TOBACCO",
    "DELI", "PIZZA", "CAFE", "BAKERY", "PHARMACY",
    "GENERAL STORE", "COUNTRY STORE", "CORNER STORE", "NEWS",
    "FOOD MART", "MINI MART", "CONVENIENCE", "GROCERY", "SUPERETTE",
    "TRADING POST", "RANCH STORE", "FEED STORE", "TRADING CO",
]


def _flag(row: dict, key: str) -> bool:
    return str(row.get(key, "")).lower() in ("true", "1", "yes")


def _float(row: dict, key: str) -> float | None:
    try:
        v = row.get(key, "")
        return float(v) if v != "" else None
    except (ValueError, TypeError):
        return None


def _int(row: dict, key: str) -> int | None:
    try:
        v = row.get(key, "")
        return int(v) if v != "" else None
    except (ValueError, TypeError):
        return None


def is_chain(name: str) -> bool:
    n = name.upper()
    return any(kw in n for kw in CHAIN_KEYWORDS)


def score_retailer(row: dict) -> int:
    s = 0
    name = row.get("name", "").upper()
    city = row.get("city", "").upper().strip()

    chain = _flag(row, "is_chain") if "is_chain" in row else is_chain(name)
    gas   = _flag(row, "is_gas")

    indie_strength = _int(row, "indie_strength")
    if indie_strength is None:
        indie_kw = any(kw in name for kw in [k.upper() for k in INDIE_KEYWORDS])
        indie_strength = 1 if indie_kw else 0

    # ── Base store type ────────────────────────────────────────────────────
    if not chain:           s += 20
    else:                   s -= 20
    if indie_strength >= 1: s += 5
    if indie_strength >= 2: s += 3
    if gas:                 s -= 5

    # ── Geographic tier ────────────────────────────────────────────────────
    if city in RURAL_CITIES:      s += 20
    elif city in TIER_A_CITIES:   s += 10
    elif city in METRO_CITIES:    s -= 15
    if city in BORDER_CITIES:     s -= 20

    # ── Population density ─────────────────────────────────────────────────
    density = _float(row, "pop_density")
    if density is not None:
        if density < 300:
            s += 20
        elif density < 1500:
            s += 10
        elif density < 5000:
            s += 0
        elif density < 12000:
            s -= 8
        else:
            s -= 15

    # ── Interstate distance ────────────────────────────────────────────────
    dist = _float(row, "interstate_dist_mi")
    if dist is not None:
        if dist > 8:
            s += 25
        elif dist > 4:
            s += 15
        elif dist > 1:
            s += 5
        elif dist > 0.5:
            s -= 5
        else:
            s -= 25

    # ── Google Places signals ──────────────────────────────────────────────
    review_count = _int(row, "review_count")
    if review_count is not None and review_count >= 0:
        if review_count < 20:
            s += 15
        elif review_count < 80:
            s += 8
        elif review_count < 300:
            s += 0
        elif review_count < 800:
            s -= 8
        else:
            s -= 20

    if _flag(row, "is_24h"):        s -= 10
    if _flag(row, "closes_early"):  s += 5
    if _flag(row, "has_lottery_type"): s += 5

    return s


_cache: list[dict] | None = None


def load_and_score() -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache
    path = AZ_CSV_ENRICHED if os.path.exists(AZ_CSV_ENRICHED) else AZ_CSV_BASE
    if not os.path.exists(path):
        return []
    retailers = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            s = score_retailer(row)
            retailers.append({
                "id":            row.get("id"),
                "name":          row.get("name", ""),
                "address":       row.get("address", ""),
                "city":          row.get("city", ""),
                "zipCode":       row.get("zipCode", ""),
                "phone":         row.get("phone", ""),
                "latitude":      row.get("latitude"),
                "longitude":     row.get("longitude"),
                "isChain":       _flag(row, "is_chain") if "is_chain" in row else is_chain(row.get("name", "")),
                "isGas":         _flag(row, "is_gas"),
                "indieStrength": _int(row, "indie_strength") or 0,
                "popDensity":    _float(row, "pop_density"),
                "interstateDist": _float(row, "interstate_dist_mi"),
                "reviewCount":   _int(row, "review_count"),
                "is24h":         _flag(row, "is_24h"),
                "closesEarly":   _flag(row, "closes_early"),
                "score":         s,
            })
    retailers.sort(key=lambda r: -r["score"])
    _cache = retailers
    return retailers
