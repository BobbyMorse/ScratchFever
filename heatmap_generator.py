"""
MA Scratch Ticket Hunter — Heatmap Generator
=============================================
Loads ma_retailers.csv, scores each retailer using a low-volume
scarcity model, then writes an interactive Folium heatmap HTML.

Usage:
  python heatmap_generator.py                        # use ma_retailers.csv
  python heatmap_generator.py --csv my_data.csv      # custom input
  python heatmap_generator.py --out hunt_map.html    # custom output
  python heatmap_generator.py --game "300X"          # highlight game keyword
"""

import argparse
import csv
from pathlib import Path

import folium
from folium.plugins import HeatMap, MarkerCluster

# ── Chain name fragments (case-insensitive) ───────────────────────────────────
CHAIN_KEYWORDS = [
    "7-ELEVEN", "7 ELEVEN", "CUMBERLAND FARMS", "CUMBERLAND",
    "MOBIL", "SHELL ", "BP ", " BP\t", "SUNOCO", "GULF ",
    "GETTY", "CITGO", "EXXON", "SPEEDWAY", "CIRCLE K",
    "WALGREENS", "CVS", "SHAWS", "SHAW'S", "STOP & SHOP",
    "MARKET BASKET", "BIG Y", "PRICE RITE", "PRICE CHOPPER",
    "HANNAFORDS", "HANNAFORD", "STAR MARKET", "TARGET",
    "WALMART", "WAL-MART", "COSTCO", "BJ'S", "BJS",
    "DOLLAR GENERAL", "DOLLAR TREE", "FAMILY DOLLAR",
    "RITE AID", "LOTTO", "LOTTERY", "GLOBAL",
]

# ── Known high-volume / border-zone cities (Tier F) ──────────────────────────
TIER_F_CITIES = {
    "METHUEN", "SALISBURY", "SEEKONK", "NORTH ATTLEBOROUGH",
    "ATTLEBORO", "REVERE", "EVERETT", "CHELSEA", "AGAWAM",
    "WEST SPRINGFIELD", "SPRINGFIELD",
}

# Positive: small-town MA (very low traffic, premium-ticket scarcity likely)
RURAL_CITIES = {
    "BARRE", "PETERSHAM", "HARDWICK", "PHILLIPSTON", "ROYALSTON",
    "WENDELL", "SHUTESBURY", "LEVERETT", "PELHAM", "WARWICK",
    "HEATH", "ROWE", "CHARLEMONT", "HAWLEY", "BLANDFORD",
    "CHESTER", "MIDDLEFIELD", "WORTHINGTON", "CUMMINGTON",
    "CHESTERFIELD", "GOSHEN", "ASHFIELD", "CONWAY", "SUNDERLAND",
    "MONTAGUE", "ERVING", "GILL", "NORTHFIELD", "WARWICK",
    "ORANGE", "NEW SALEM", "OAKHAM", "HUBBARDSTON", "TEMPLETON",
    "WINCHENDON", "GARDNER",
}

# Residential/off-highway secondary towns (Tier A)
TIER_A_CITIES = {
    "WESTFIELD", "WARE", "BELCHERTOWN", "PALMER", "MONSON",
    "WALES", "BRIMFIELD", "STURBRIDGE", "BROOKFIELD", "EAST BROOKFIELD",
    "SPENCER", "LEICESTER", "PAXTON", "HOLDEN", "PRINCETON",
    "WESTBORO", "SOUTHBORO", "NORTHBORO", "GRAFTON", "UPTON",
    "MENDON", "DOUGLAS", "UXBRIDGE", "MILLVILLE", "BLACKSTONE",
    "BERKLEY", "DIGHTON", "SWANSEA", "SOMERSET", "REHOBOTH",
    "PLAINVILLE", "WRENTHAM", "NORFOLK", "MILLIS", "MEDWAY",
    "MEDFIELD", "SHERBORN", "HOLLISTON", "HOPKINTON", "MILFORD",
    "HOPEDALE", "BELLINGHAM", "FRANKLIN", "FOXBORO", "MANSFIELD",
    "NORTON", "RAYNHAM", "TAUNTON", "MIDDLEBORO", "WAREHAM",
    "MATTAPOISETT", "ROCHESTER", "LAKEVILLE", "FREETOWN", "ASSONET",
    "FALL RIVER", "NEW BEDFORD", "ACUSHNET", "FAIRHAVEN",
    "DARTMOUTH", "WESTPORT", "ACOAXET",
}

# Independent store type keywords (positive soft signal)
INDIE_KEYWORDS = [
    "VARIETY", "LIQUOR", "PACKAGE", "SMOKE", "CIGAR", "TOBACCO",
    "DELI", "PIZZA", "CAFÉ", "CAFE", "BAKERY", "PHARMACY",
    "GENERAL STORE", "COUNTRY STORE", "CORNER STORE", "NEWS",
    "STATIONERS", "FOOD MART", "MINI MART", "CONVENIENCE",
    "GROCERY", "MARKET", "SUPERETTE", "FARM",
]


def is_chain(name: str) -> bool:
    n = name.upper()
    return any(kw in n for kw in CHAIN_KEYWORDS)


def score_retailer(row: dict) -> int:
    """Return a 0-100+ scarcity score. Higher = better hunting target."""
    s = 0
    name = row.get("name", "").upper()
    city = row.get("city", "").upper().strip()
    keno_monitor = str(row.get("kenoMonitor", "")).lower() in ("true", "1", "yes")
    wol_monitor  = str(row.get("wolMonitor",  "")).lower() in ("true", "1", "yes")
    self_svc     = str(row.get("selfService",  "")).lower() in ("true", "1", "yes")
    chain        = is_chain(name)

    indie_keyword = any(kw in name for kw in [k.upper() for k in INDIE_KEYWORDS])

    # ── LOW-VOLUME POSITIVE SIGNALS ────────────────────────────────────────
    if not chain:
        s += 20        # independent store
    if indie_keyword:
        s += 5         # sounds like a small store
    if not keno_monitor:
        s += 10        # no Keno monitor = less gambling-focused foot traffic
    if not wol_monitor:
        s += 5         # no WoL monitor
    if not self_svc:
        s += 5         # no self-service = more human-run, lower volume feel

    if city in RURAL_CITIES:
        s += 20        # small western/central MA town
    elif city in TIER_A_CITIES:
        s += 15        # residential secondary-road town

    # ── HIGH-VOLUME NEGATIVE SIGNALS ──────────────────────────────────────
    if chain:
        s -= 20
    if keno_monitor:
        s -= 15        # Keno monitor = constant lottery traffic
    if wol_monitor:
        s -= 10        # WoL monitor = same
    if self_svc:
        s -= 10        # self-service terminal = higher automated volume

    if city in TIER_F_CITIES:
        s -= 25        # border zone / known-high-volume corridor

    return s


TIER_LABELS = {
    "ELITE":  {"min": 45, "color": "#00cc44", "label": "Elite Hunt (45+)"},
    "GOOD":   {"min": 25, "color": "#88cc00", "label": "Worth Checking (25–44)"},
    "CAUTION":{"min": 10, "color": "#ffaa00", "label": "Caution (10–24)"},
    "AVOID":  {"min": -999,"color": "#cc2200", "label": "Avoid (<10)"},
}


def tier(score: int) -> str:
    if score >= 45:
        return "ELITE"
    if score >= 25:
        return "GOOD"
    if score >= 10:
        return "CAUTION"
    return "AVOID"


def popup_html(row: dict, score: int, game_kw: str) -> str:
    t = tier(score)
    tier_info = TIER_LABELS[t]
    keno_mon = str(row.get("kenoMonitor","")).lower() in ("true","1","yes")
    wol_mon  = str(row.get("wolMonitor","")).lower()  in ("true","1","yes")
    self_svc = str(row.get("selfService","")).lower()  in ("true","1","yes")
    chain_flag = "YES ⚠" if is_chain(row.get("name","")) else "No"
    km_flag  = "YES ⚠" if keno_mon else "No"
    ss_flag  = "YES" if self_svc else "No"

    game_line = ""
    if game_kw:
        game_line = f"<div style='color:#ff9900;font-weight:bold;'>🎯 Target game: {game_kw}</div>"

    addr = ", ".join(filter(None, [
        row.get("address",""), row.get("address2",""),
        row.get("city",""), row.get("zipCode",""),
    ]))

    return f"""
<div style="font-family:sans-serif;font-size:13px;min-width:220px;">
  <b style="font-size:15px;">{row.get("name","")}</b><br>
  <span style="color:{tier_info['color']};font-weight:bold;">
    ▶ {tier_info['label']} — Score {score}
  </span><br>
  {game_line}
  <hr style="margin:4px 0;">
  {addr}<br>
  📞 {row.get("phone","")}<br>
  <hr style="margin:4px 0;">
  <b>Chain:</b> {chain_flag}<br>
  <b>Keno Monitor:</b> {km_flag}<br>
  <b>Self-Service:</b> {ss_flag}<br>
  <b>Games:</b> {row.get("games","")}<br>
  <b>ADA:</b> {row.get("adaCompliant","")}<br>
</div>""".strip()


def build_map(retailers: list[dict], game_kw: str = "") -> folium.Map:
    scored = []
    for row in retailers:
        s = score_retailer(row)
        scored.append((s, row))
    scored.sort(key=lambda x: -x[0])

    ma_center = [42.1, -71.8]
    m = folium.Map(
        location=ma_center,
        zoom_start=9,
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    )

    # ── Heat layer — all stores, score-weighted ────────────────────────────
    heat_data = []
    for s, row in scored:
        lat = row.get("latitude")
        lon = row.get("longitude")
        if lat and lon:
            heat_data.append([float(lat), float(lon), max(1, s + 30)])

    HeatMap(
        heat_data,
        name="Score Heatmap",
        min_opacity=0.35,
        max_zoom=14,
        radius=20,
        blur=22,
        gradient={0.2: "#0000ff", 0.45: "#00ff00", 0.65: "#ffff00", 0.85: "#ff8800", 1.0: "#ff0000"},
    ).add_to(m)

    # ── Clickable markers — Elite & Good only (keeps file small) ──────────
    gk_upper = game_kw.upper() if game_kw else ""

    elite_fg = folium.FeatureGroup(name="Elite Hunt (45+)", show=True)
    good_fg  = folium.FeatureGroup(name="Worth Checking (25-44)", show=True)
    m.add_child(elite_fg)
    m.add_child(good_fg)

    for s, row in scored:
        t_key = tier(s)
        if t_key not in ("ELITE", "GOOD"):
            continue
        lat = row.get("latitude")
        lon = row.get("longitude")
        if not lat or not lon:
            continue

        t_info = TIER_LABELS[t_key]
        game_match = gk_upper and gk_upper in row.get("name", "").upper()
        radius = 9 if t_key == "ELITE" else 7

        folium.CircleMarker(
            location=[float(lat), float(lon)],
            radius=radius,
            color=t_info["color"],
            fill=True,
            fill_color="#ffff00" if game_match else t_info["color"],
            fill_opacity=0.9,
            popup=folium.Popup(popup_html(row, s, game_kw if game_match else ""), max_width=280),
            tooltip=f"{row.get('name','')} — {row.get('city','')} — Score {s}",
        ).add_to(elite_fg if t_key == "ELITE" else good_fg)

    folium.LayerControl(collapsed=False).add_to(m)

    # ── Legend ────────────────────────────────────────────────────────────
    elite_count   = sum(1 for s, _ in scored if tier(s) == "ELITE")
    good_count    = sum(1 for s, _ in scored if tier(s) == "GOOD")
    caution_count = sum(1 for s, _ in scored if tier(s) == "CAUTION")
    avoid_count   = sum(1 for s, _ in scored if tier(s) == "AVOID")

    legend_html = f"""
<div style="
    position:fixed;bottom:40px;right:15px;z-index:1000;
    background:white;padding:12px 16px;border-radius:8px;
    box-shadow:0 2px 8px rgba(0,0,0,0.3);font-family:sans-serif;font-size:13px;
    min-width:200px;">
  <b style="font-size:14px;">🎯 Scratch Hunter</b><br>
  <span style="font-size:11px;color:#666;">MA Retailer Scarcity Score</span>
  <hr style="margin:6px 0;">
  <div><span style="color:#00cc44;font-size:18px;">●</span> Elite Hunt 45+ &nbsp;<b>({elite_count})</b></div>
  <div><span style="color:#88cc00;font-size:18px;">●</span> Worth Checking 25–44 &nbsp;<b>({good_count})</b></div>
  <div><span style="color:#ffaa00;font-size:18px;">●</span> Caution 10–24 &nbsp;<b>({caution_count})</b></div>
  <div><span style="color:#cc2200;font-size:18px;">●</span> Avoid &lt;10 &nbsp;<b>({avoid_count})</b></div>
  <hr style="margin:6px 0;">
  <span style="font-size:11px;color:#888;">Total retailers: {len(retailers)}</span>
</div>""".strip()

    m.get_root().html.add_child(folium.Element(legend_html))
    return m


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",  default="ma_retailers.csv", help="Input CSV from retailer_scraper.py")
    parser.add_argument("--out",  default="ma_heatmap.html",  help="Output HTML heatmap file")
    parser.add_argument("--game", default="",                 help="Target game keyword (e.g. 300X)")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found. Run retailer_scraper.py first.")
        return

    retailers = load_csv(csv_path)
    print(f"Loaded {len(retailers)} retailers from {csv_path}")

    m = build_map(retailers, game_kw=args.game)

    out_path = Path(args.out)
    m.save(str(out_path))
    print(f"Heatmap saved -> {out_path}")
    print(f"Open in browser: {out_path.resolve()}")

    # Print top 20
    scored = sorted(
        [(score_retailer(r), r) for r in retailers],
        key=lambda x: -x[0]
    )
    print("\n-- TOP 20 HUNT TARGETS ----------------------------------------")
    print(f"{'Score':>5}  {'Name':<35} {'City':<20} {'Keno Mon':>8}  {'Chain':>5}")
    print("-" * 80)
    for s, r in scored[:20]:
        km  = "YES" if str(r.get("kenoMonitor","")).lower() in ("true","1","yes") else "no"
        chn = "YES" if is_chain(r.get("name","")) else "no"
        print(f"  {s:>3}  {r.get('name',''):<35} {r.get('city',''):<20} {km:>8}  {chn:>5}")


if __name__ == "__main__":
    main()
