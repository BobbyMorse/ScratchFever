"""
Quick diagnostic for DE OCR. Confirms the Anthropic key is set, the model
responds, and the prompt parses correctly against a known-good DE image.

Run on Railway shell or locally with:
  python scripts/diagnose_de_ocr.py
"""
import json
import logging
import os
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from backend.scraper.states import de_ocr  # noqa: E402

SAMPLE_URL = (
    "https://www.delottery.com/Content/images/instant-lottery/"
    "instant-details/DE512OSv3.jpg"
)


def main() -> int:
    print(f"ANTHROPIC_API_KEY present: {bool(os.getenv('ANTHROPIC_API_KEY'))}")
    print(f"Sample image: {SAMPLE_URL}")
    print(f"Cache file: {de_ocr.CACHE_FILE} (exists={de_ocr.CACHE_FILE.exists()})")
    print()
    print("Calling OCR (this hits the Anthropic API)…")

    result = de_ocr.ocr_image(SAMPLE_URL)
    if result is None:
        print("FAIL: ocr_image returned None. Check the warnings above for the")
        print("specific failure (missing key, fetch error, API error, parse error).")
        return 1

    print()
    print("OCR OK. Top-line stats:")
    print(f"  tiers: {len(result['tiers'])}")
    print(f"  total_tickets: {result.get('total_tickets')}")
    print(f"  overall_odds_one_in: {result.get('overall_odds_one_in')}")
    print()
    print("First 3 tiers:")
    for t in result["tiers"][:3]:
        print(f"  {t}")
    print()
    print("Full JSON:")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
