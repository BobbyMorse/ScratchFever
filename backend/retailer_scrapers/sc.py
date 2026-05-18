"""
South Carolina Education Lottery retailer scraper.
POST to sceducationlottery.com/Search/SearchRetailer with each SC zip code.
Returns an HTML table with columns: Business Name, Address, City, County, Zip Code.
Requires CSRF token and session cookie from the locator page.
"""
from __future__ import annotations
import logging
import re
import time
from html.parser import HTMLParser
from .base import make_external_id, upsert_retailers

logger = logging.getLogger(__name__)

LOCATOR_URL = "https://www.sceducationlottery.com/Games/HowToPlay"
SEARCH_URL = "https://www.sceducationlottery.com/Search/SearchRetailer"

# SC zip codes: 29001–29948 range (all valid SC zips)
SC_ZIPS = [
    # Aiken / Edgefield / McCormick
    "29801", "29803", "29805", "29809", "29810", "29812", "29816", "29817",
    "29819", "29821", "29824", "29826", "29827", "29828", "29829", "29831",
    "29832", "29834", "29835", "29836", "29838", "29840", "29841", "29842",
    "29843", "29844", "29845", "29846", "29847", "29848", "29849", "29850",
    "29851", "29853", "29856", "29860", "29861",
    # Anderson / Oconee / Pickens / Greenwood / Laurens / Abbeville
    "29620", "29621", "29624", "29625", "29626", "29627", "29628", "29630",
    "29631", "29634", "29635", "29638", "29639", "29640", "29641", "29642",
    "29643", "29644", "29645", "29646", "29647", "29648", "29649", "29650",
    "29651", "29652", "29653", "29654", "29655", "29656", "29657", "29658",
    "29659", "29661", "29662", "29664", "29665", "29666", "29667", "29669",
    "29670", "29671", "29672", "29673", "29676", "29678", "29680", "29681",
    "29682", "29683", "29684", "29685", "29686", "29687", "29688", "29689",
    "29690", "29691", "29692", "29693", "29696", "29697",
    # Beaufort / Colleton / Hampton / Jasper
    "29901", "29902", "29903", "29904", "29905", "29906", "29907", "29909",
    "29910", "29911", "29912", "29913", "29914", "29915", "29916", "29918",
    "29920", "29921", "29922", "29923", "29924", "29925", "29926", "29927",
    "29928", "29929", "29931", "29932", "29933", "29934", "29935", "29936",
    "29938", "29939", "29940", "29941", "29943", "29944", "29945",
    # Charleston / Berkeley / Dorchester
    "29401", "29403", "29404", "29405", "29406", "29407", "29409", "29410",
    "29412", "29414", "29417", "29418", "29420", "29422", "29423", "29424",
    "29425", "29426", "29429", "29431", "29432", "29433", "29434", "29435",
    "29436", "29437", "29438", "29439", "29440", "29442", "29445", "29446",
    "29447", "29448", "29449", "29450", "29451", "29452", "29453", "29455",
    "29456", "29457", "29458", "29461", "29464", "29466", "29468", "29469",
    "29470", "29471", "29472", "29474", "29475", "29476", "29477", "29479",
    "29480", "29481", "29482", "29483", "29484", "29485", "29486", "29487",
    "29488", "29492",
    # Columbia / Richland / Lexington / Newberry / Fairfield / Chester
    "29201", "29202", "29203", "29204", "29205", "29206", "29207", "29208",
    "29209", "29210", "29211", "29212", "29214", "29215", "29216", "29217",
    "29218", "29219", "29220", "29221", "29222", "29223", "29224", "29225",
    "29226", "29227", "29228", "29229", "29230", "29240", "29250", "29260",
    "29290", "29292",
    "29002", "29006", "29010", "29016", "29018", "29020", "29030", "29031",
    "29032", "29033", "29036", "29038", "29039", "29040", "29041", "29042",
    "29044", "29045", "29046", "29047", "29048", "29051", "29052", "29053",
    "29054", "29055", "29056", "29058", "29059", "29061", "29062", "29063",
    "29065", "29067", "29069", "29070", "29071", "29072", "29073", "29074",
    "29075", "29078", "29079", "29080", "29081", "29082",
    # Greenville / Spartanburg / Cherokee / Union / York / Lancaster
    "29301", "29302", "29303", "29304", "29305", "29306", "29307", "29316",
    "29320", "29321", "29322", "29323", "29324", "29325", "29326", "29327",
    "29328", "29329", "29330", "29331", "29332", "29333", "29334", "29335",
    "29336", "29338", "29340", "29341", "29342", "29346", "29348", "29349",
    "29351", "29353", "29355", "29356", "29360", "29364", "29365", "29368",
    "29369", "29370", "29372", "29373", "29374", "29375", "29376", "29377",
    "29378", "29379", "29384", "29385", "29386", "29388", "29395", "29401",
    # York / Rock Hill / Lancaster / Chesterfield / Marlboro
    "29702", "29703", "29704", "29706", "29707", "29708", "29709", "29710",
    "29712", "29714", "29715", "29716", "29717", "29718", "29720", "29721",
    "29722", "29724", "29726", "29727", "29728", "29729", "29730", "29731",
    "29732", "29733", "29734", "29741", "29742", "29743", "29744", "29745",
    # Florence / Darlington / Dillon / Marlboro / Marion / Horry
    "29501", "29502", "29503", "29504", "29505", "29506", "29510", "29511",
    "29512", "29516", "29518", "29519", "29520", "29525", "29526", "29527",
    "29528", "29530", "29532", "29536", "29540", "29541", "29543", "29544",
    "29545", "29546", "29547", "29550", "29551", "29554", "29555", "29556",
    "29560", "29563", "29564", "29565", "29566", "29567", "29568", "29569",
    "29570", "29571", "29572", "29574", "29575", "29576", "29577", "29578",
    "29579", "29580", "29581", "29582", "29583", "29584", "29585", "29587",
    "29588", "29589", "29590", "29591", "29592", "29593", "29594", "29596",
    # Sumter / Lee / Clarendon / Orangeburg / Calhoun / Bamberg / Barnwell
    "29001", "29003", "29006", "29009", "29010", "29011", "29014", "29015",
    "29016", "29018", "29101", "29102", "29104", "29105", "29107", "29108",
    "29111", "29112", "29113", "29114", "29115", "29116", "29117", "29118",
    "29122", "29123", "29125", "29126", "29127", "29128", "29129", "29130",
    "29131", "29133", "29135", "29137", "29138", "29142", "29143", "29145",
    "29146", "29147", "29148", "29150", "29151", "29152", "29153", "29154",
    "29160", "29161", "29162", "29163", "29164", "29166", "29168", "29169",
    "29170", "29171", "29172", "29174", "29175", "29176", "29177", "29178",
    "29180",
]


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_td = False
        self._cells: list[str] = []
        self._buf: list[str] = []

    @property
    def cells(self):
        return self._cells

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._in_td = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "td":
            self._cells.append("".join(self._buf).strip())
            self._in_td = False

    def handle_data(self, data):
        if self._in_td:
            self._buf.append(data)


def _parse_results(html: str) -> list[dict]:
    """Extract rows from the 5-column results table: Name, Address, City, County, Zip."""
    parser = _TableParser()
    parser.feed(html)
    cells = parser.cells
    retailers: list[dict] = []
    i = 0
    while i + 4 < len(cells):
        name = cells[i].strip()
        address = cells[i + 1].strip()
        city = cells[i + 2].strip()
        # county = cells[i + 3] — skip
        zip_code = cells[i + 4].strip()
        i += 5
        if not name or name.lower() in ("business name", ""):
            continue
        if not re.search(r"[A-Za-z]", name):
            continue
        retailers.append({
            "external_id": make_external_id(name, address, zip_code),
            "name": name,
            "address": address or None,
            "city": city or None,
            "zip_code": zip_code or None,
            "phone": None,
            "latitude": None,
            "longitude": None,
        })
    return retailers


def scrape_sc() -> list[dict]:
    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": LOCATOR_URL,
    })

    # Get session cookie + CSRF token
    try:
        init_resp = session.get(LOCATOR_URL, timeout=30)
        token_match = re.search(
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"',
            init_resp.text,
        )
        if not token_match:
            logger.warning("SC: could not find CSRF token")
            return []
        csrf_token = token_match.group(1)
    except Exception as e:
        logger.warning("SC: failed to init session: %s", e)
        return []

    zips = list(dict.fromkeys(SC_ZIPS))
    logger.info("SC: scraping %d zip codes", len(zips))

    all_retailers: list[dict] = []
    seen_ids: set[str] = set()

    for i, zipcode in enumerate(zips):
        try:
            resp = session.post(
                SEARCH_URL,
                data={
                    "__RequestVerificationToken": csrf_token,
                    "retailerOption": "ZipCode",
                    "retailerInput": zipcode,
                },
                timeout=30,
            )
            resp.raise_for_status()
            rows = _parse_results(resp.text)
            new_count = 0
            for r in rows:
                if r["external_id"] not in seen_ids:
                    seen_ids.add(r["external_id"])
                    all_retailers.append(r)
                    new_count += 1
            # Refresh CSRF token from response for next request
            token_match = re.search(
                r'name="__RequestVerificationToken"[^>]*value="([^"]+)"',
                resp.text,
            )
            if token_match:
                csrf_token = token_match.group(1)
        except Exception as e:
            logger.debug("SC: error on zip %s: %s", zipcode, e)
        time.sleep(0.3)

        if (i + 1) % 100 == 0:
            logger.info("SC: progress %d/%d zips, %d retailers", i + 1, len(zips), len(all_retailers))

    logger.info("SC: scraped %d unique retailers", len(all_retailers))
    return all_retailers


async def run(conn) -> int:
    import asyncio
    retailers = await asyncio.to_thread(scrape_sc)
    return await upsert_retailers(conn, "SC", retailers)
