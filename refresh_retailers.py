"""
Monthly retailer refresh — fetches all states then imports to Supabase.

Run once a month (or on demand) from the project root:
    python refresh_retailers.py

To skip fetch and only re-import existing CSVs:
    python refresh_retailers.py --import-only

To refresh a single state:
    python refresh_retailers.py --states az ga

DC is intentionally excluded — source HTML has no lat/lng for mapping.
MA is import-only — data comes from the MA lottery portal, not a fetch script.
RI fetch requires Playwright for auth header capture (run manually if needed).
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent

# (state_code, fetch_script, import_script, notes)
STATES = [
    ("az", "fetch_az_retailers.py",  "import_az_retailers.py",  None),
    ("ga", "fetch_ga_retailers.py",  "import_ga_retailers.py",  None),
    ("fl", "fetch_fl_retailers.py",  "import_fl_retailers.py",  None),
    ("ny", "fetch_ny_retailers.py",  "import_ny_retailers.py",  None),
    ("ri", "fetch_ri_retailers.py",  "import_ri_retailers.py",  "requires Playwright"),
    ("ma", None,                     "import_ma_retailers.py",  "import only — no fetch script"),
]


def run(script: str, label: str) -> bool:
    print(f"\n{'-'*60}")
    print(f"  {label}")
    print(f"  {script}")
    print(f"{'-'*60}")
    start = time.time()
    result = subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=ROOT,
    )
    elapsed = time.time() - start
    ok = result.returncode == 0
    status = "OK" if ok else f"FAILED (exit {result.returncode})"
    print(f"  -> {status}  [{elapsed:.1f}s]")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Monthly retailer refresh")
    parser.add_argument("--import-only", action="store_true",
                        help="Skip fetch scripts, only run imports")
    parser.add_argument("--states", nargs="+", metavar="STATE",
                        help="Only refresh these state codes (e.g. az ga fl)")
    args = parser.parse_args()

    filter_states = {s.lower() for s in args.states} if args.states else None

    active = [s for s in STATES if filter_states is None or s[0] in filter_states]
    if not active:
        print(f"No matching states for: {args.states}")
        sys.exit(1)

    print(f"\nScratchFrenzy retailer refresh — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"States: {', '.join(s[0].upper() for s in active)}")
    if args.import_only:
        print("Mode: import-only (skipping fetch)")

    results: dict[str, list[str]] = {"ok": [], "failed": [], "skipped": []}

    for state, fetch_script, import_script, note in active:
        state_upper = state.upper()

        # Fetch
        if not args.import_only and fetch_script:
            ok = run(fetch_script, f"[{state_upper}] fetch")
            if not ok:
                print(f"  !! Fetch failed for {state_upper} — skipping import")
                results["failed"].append(f"{state_upper} fetch")
                results["skipped"].append(f"{state_upper} import")
                continue
            results["ok"].append(f"{state_upper} fetch")
        elif not fetch_script:
            print(f"\n  [{state_upper}] fetch skipped — {note}")
            results["skipped"].append(f"{state_upper} fetch")

        # Import
        csv_file = ROOT / f"{state}_retailers.csv"
        if not args.import_only and fetch_script and not csv_file.exists():
            print(f"  !! CSV not found after fetch: {csv_file.name} — skipping import")
            results["skipped"].append(f"{state_upper} import")
            continue

        if state == "ma":
            # MA uses enriched CSV when available
            enriched = ROOT / "ma_retailers_enriched.csv"
            if not enriched.exists() and not csv_file.exists():
                print(f"\n  [{state_upper}] import skipped — no CSV found")
                results["skipped"].append(f"{state_upper} import")
                continue

        ok = run(import_script, f"[{state_upper}] import")
        if ok:
            results["ok"].append(f"{state_upper} import")
        else:
            results["failed"].append(f"{state_upper} import")

    # Summary
    print(f"\n{'='*60}")
    print(f"  DONE -- {datetime.now().strftime('%H:%M:%S')}")
    print(f"  OK:      {', '.join(results['ok']) or 'none'}")
    print(f"  Failed:  {', '.join(results['failed']) or 'none'}")
    print(f"  Skipped: {', '.join(results['skipped']) or 'none'}")
    print(f"{'='*60}\n")

    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
