"""
Delaware game-image OCR via Claude vision.

Each DE scratch-off has a marketing image at
  https://www.delottery.com/Content/images/instant-lottery/instant-details/DE<NUM>OSv<N>.jpg
that embeds the full prize-tier table:
  WIN WITH PRIZE(S) OF | WIN | ODDS of 1 IN | WINNERS | PRIZE VALUE
plus the total tickets ordered and the overall 1-in odds.

DE's published PDF only carries top-prize + 2nd-tier remainders, so without
OCR we can't compute EV. With OCR we get every tier's odds + original
prizes_total, which combined with the PDF remainders gives us a full hybrid
EV that reflects sell-through.

OCR results are immutable per print run (the image variant suffix changes
when DE re-prints), so we cache by full image URL in de_odds_cache.json
and only re-OCR on cache miss.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from pathlib import Path

import anthropic
import requests

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent / "de_odds_cache.json"
_MODEL = "claude-haiku-4-5-20251001"

# Soft wall so a cold cache doesn't blow the runner's 120s per-state budget.
# Anything not OCR'd this cycle is picked up next run.
_OCR_BUDGET_S = 60
# Persist partial progress periodically so we don't lose work on timeout.
_CACHE_SAVE_EVERY = 5
# DE's largest image is ~3300x1950. Vision accepts up to ~5MB, well under cap.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

_OCR_INSTRUCTIONS = """\
You are reading a US state lottery scratch-off marketing image. The image \
contains a "GAME INFORMATION" table with these columns:
  WIN WITH PRIZE(S) OF | WIN | ODDS of 1 IN | WINNERS | PRIZE VALUE

It also contains a summary line near the bottom of the table with the form:
  "<N> prizes with a value of $<V>. Average odds of winning are 1 in <O>. \
Prizes as a percent of ticket sales: <P>%. All odds and percentages based \
on selling all <T> $<PRICE> tickets ordered for this game."

Return ONLY a JSON object with this exact schema (no prose, no code fences):
{
  "tiers": [
    {"prize_amount": <number, the WIN column as plain dollars>,
     "odds_one_in": <number, the ODDS of 1 IN column>,
     "prizes_total": <integer, the WINNERS column>}
  ],
  "total_tickets": <integer, total tickets ordered from the summary line, or null>,
  "overall_odds_one_in": <number, "1 in X" from the summary, or null>
}

Rules:
- One entry per row in the GAME INFORMATION table. Skip header rows and any
  footer/disclaimer rows that do not have all three numeric columns.
- prize_amount is the cash payout (the "WIN" column), NOT the "WIN WITH" \
expression. e.g. row "$1 x 2 | $2 | 50 | 7,800" → prize_amount=2.
- Strip commas from all numbers. "39,000" → 39000.
- Use null (not 0, not "") for any field you genuinely cannot read.
- If the image is not a DE scratch-off prize table at all, return \
{"tiers": [], "total_tickets": null, "overall_odds_one_in": null}.
- Return ONLY the JSON object."""

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic | None:
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            return None
        _client = anthropic.Anthropic(api_key=key)
    return _client


# ── cache ─────────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("DE OCR: cache save failed: %s", e)


# ── OCR ───────────────────────────────────────────────────────────────────────

def _fetch_image_bytes(url: str, session: requests.Session | None = None) -> bytes | None:
    s = session or requests
    try:
        r = s.get(url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning("DE OCR: image fetch failed %s: %s", url, e)
        return None
    if len(r.content) > _MAX_IMAGE_BYTES:
        logger.warning("DE OCR: image too large %d bytes %s", len(r.content), url)
        return None
    return r.content


def _parse_response(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except Exception:
        logger.warning("DE OCR: non-JSON response: %r", text[:300])
        return None
    if not isinstance(data, dict):
        return None
    tiers_raw = data.get("tiers") or []
    if not isinstance(tiers_raw, list):
        return None

    tiers: list[dict] = []
    for t in tiers_raw:
        if not isinstance(t, dict):
            continue
        prize = _num(t.get("prize_amount"))
        odds = _num(t.get("odds_one_in"))
        total = _int(t.get("prizes_total"))
        # Need at minimum prize+odds to contribute to EV.
        if prize is None or prize <= 0 or odds is None or odds <= 0:
            continue
        tiers.append({
            "prize_amount": prize,
            "odds_one_in": odds,
            "prizes_total": total,
        })

    return {
        "tiers": tiers,
        "total_tickets": _int(data.get("total_tickets")),
        "overall_odds_one_in": _num(data.get("overall_odds_one_in")),
    }


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).replace(",", "").replace("$", "").strip()
        return float(s) if s else None
    except Exception:
        return None


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def ocr_image(image_url: str, session: requests.Session | None = None) -> dict | None:
    """OCR one DE game image. Returns the parsed dict or None on any failure.
    Caller is responsible for caching."""
    client = _get_client()
    if not client:
        logger.warning("DE OCR: ANTHROPIC_API_KEY not set, skipping")
        return None

    img = _fetch_image_bytes(image_url, session=session)
    if not img:
        return None
    b64 = base64.standard_b64encode(img).decode("ascii")

    try:
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=[{
                "type": "text",
                "text": _OCR_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    }},
                    {"type": "text", "text": "Extract the prize table now."},
                ],
            }],
        )
    except Exception as e:
        logger.warning("DE OCR: API call failed for %s: %s", image_url, e)
        return None

    text = "".join(getattr(b, "text", "") for b in msg.content)
    return _parse_response(text)


def ocr_image_cached(
    image_url: str,
    cache: dict,
    session: requests.Session | None = None,
) -> dict | None:
    """Return cached OCR result if present, else OCR and store. Returns None on failure."""
    if not image_url:
        return None
    hit = cache.get(image_url)
    if isinstance(hit, dict) and "tiers" in hit:
        return hit
    parsed = ocr_image(image_url, session=session)
    if parsed is None:
        return None
    cache[image_url] = parsed
    return parsed


def ocr_batch(
    image_urls: list[str],
    cache: dict | None = None,
    session: requests.Session | None = None,
    budget_s: float = _OCR_BUDGET_S,
) -> dict[str, dict]:
    """OCR a list of image URLs, respecting the time budget. Returns a dict of
    {image_url: parsed_dict} for everything successfully resolved (cache hits
    plus fresh OCRs)."""
    if cache is None:
        cache = load_cache()
    out: dict[str, dict] = {}
    started = time.monotonic()
    fresh = 0

    for url in image_urls:
        if not url:
            continue
        hit = cache.get(url)
        if isinstance(hit, dict) and "tiers" in hit:
            out[url] = hit
            continue
        if time.monotonic() - started > budget_s:
            logger.info(
                "DE OCR: time budget %.0fs exhausted, %d new images deferred",
                budget_s, len(image_urls) - len(out),
            )
            break
        parsed = ocr_image(url, session=session)
        if parsed is None:
            continue
        cache[url] = parsed
        out[url] = parsed
        fresh += 1
        if fresh % _CACHE_SAVE_EVERY == 0:
            save_cache(cache)

    if fresh:
        save_cache(cache)
    return out
