"""
Vision-feasibility spike for the photo-crowdsourcing flywheel.

Question we are answering: given a phone photo of a scratch-ticket dispenser
wall, can an LLM with vision reliably extract (a) which games are visible and
(b) a coarse inventory level per game? This is the load-bearing assumption
under the photo-crowdsource → caller-replacement strategy, so it has to be
tested before any more product/monetization decisions.

Usage
-----
1. Drop one or more JPG/PNG photos into vision_test/photos/
   (real phone snapshots are the point; stock photos only set a ceiling.)
2. Set ANTHROPIC_API_KEY in the env (.env already loaded by python-dotenv if
   installed).
3. python vision_test/run.py
4. Read vision_test/results/latest.json and the printed summary.

For each photo we ask Sonnet 4.6 for structured JSON:
- list of visible games (name as printed, ticket price if visible, coarse
  inventory band, confidence)
- photo quality assessment (usable / partial / unusable)

Sonnet is the "best case ceiling" — if it can't, the answer to the
feasibility question is no. Haiku 4.5 is the cost-realistic production
target; once Sonnet is good enough we re-run on the same corpus with Haiku
to measure the drop.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import anthropic

ROOT = Path(__file__).resolve().parent
PHOTOS_DIR = ROOT / "photos"
RESULTS_DIR = ROOT / "results"

MODEL_BASELINE = "claude-sonnet-4-6"
MODEL_CHEAP = "claude-haiku-4-5-20251001"

# Cacheable instructions — same on every request, so prompt-caching makes
# multi-photo batches cheap after the first call.
EXTRACTION_INSTRUCTIONS = """\
You are inspecting a photo taken inside a US convenience store / lottery
retailer. Your job is to extract structured data about the scratch-off
lottery tickets visible in the photo, exactly as they would appear in a
crowdsourced inventory feed.

Return ONE JSON object with this shape:

{
  "photo_quality": "usable" | "partial" | "unusable",
  "photo_quality_reason": "<one short clause — glare, angle, blur, no tickets visible, etc.>",
  "games": [
    {
      "name_as_printed": "<exact text on the ticket face / dispenser label, or null>",
      "ticket_price_usd": <number or null — only if clearly visible on dispenser tag>,
      "inventory_band": "empty" | "low" | "medium" | "high" | "full" | "unknown",
      "inventory_reason": "<one short clause — visible ticket depth, dispenser fullness cue>",
      "identification_confidence": "high" | "medium" | "low"
    }
  ],
  "overall_notes": "<one short clause on anything unusual — partial wall, behind glass, etc.>"
}

Rules:
- Only include games you can actually see in the photo. Do not invent.
- "name_as_printed" should be the actual printed name on the ticket front
  (e.g. "Diamond Millions", "$1,000,000 Cashword"). Do NOT guess game numbers
  unless they are visibly printed.
- "inventory_band" should be based on how full the dispenser appears
  (visible ticket stack thickness, gap behind the front ticket, "low" tag,
  empty slot). If you genuinely cannot tell, use "unknown" — do not guess.
- If the photo shows no scratch tickets at all, return games: [] and set
  photo_quality to "unusable".
- Output ONLY the JSON object. No prose, no markdown fences.
"""


@dataclass
class PhotoResult:
    path: Path
    model: str
    raw_text: str
    parsed: dict[str, Any] | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    error: str | None = None


def _encode_image(path: Path) -> tuple[str, str]:
    mime, _ = mimetypes.guess_type(path.name)
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise ValueError(f"unsupported image type for {path.name}: {mime}")
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return mime, data


def run_one(client: anthropic.Anthropic, path: Path, model: str) -> PhotoResult:
    mime, data = _encode_image(path)
    start = time.perf_counter()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=[
                {
                    "type": "text",
                    "text": EXTRACTION_INSTRUCTIONS,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": data},
                        },
                        {
                            "type": "text",
                            "text": "Extract the scratch-ticket inventory from this photo.",
                        },
                    ],
                }
            ],
        )
    except Exception as e:  # boundary call to external API
        return PhotoResult(
            path=path, model=model, raw_text="", parsed=None,
            latency_ms=int((time.perf_counter() - start) * 1000),
            input_tokens=0, output_tokens=0, cache_read_tokens=0,
            error=f"{type(e).__name__}: {e}",
        )

    latency_ms = int((time.perf_counter() - start) * 1000)
    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

    parsed: dict[str, Any] | None = None
    parse_err: str | None = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        parse_err = f"JSON parse failed: {e}"

    return PhotoResult(
        path=path, model=model, raw_text=raw, parsed=parsed,
        latency_ms=latency_ms,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        error=parse_err,
    )


def summarize(results: list[PhotoResult]) -> str:
    if not results:
        return "No photos processed."
    lines = ["", "=== VISION TEST SUMMARY ===", ""]
    total_in = total_out = total_cache = 0
    total_games = usable = 0
    for r in results:
        total_in += r.input_tokens
        total_out += r.output_tokens
        total_cache += r.cache_read_tokens
        if r.parsed:
            quality = r.parsed.get("photo_quality", "?")
            games = r.parsed.get("games", []) or []
            total_games += len(games)
            if quality == "usable":
                usable += 1
            game_blurb = ", ".join(
                f"{g.get('name_as_printed','?')} [{g.get('inventory_band','?')}]"
                for g in games[:6]
            )
            if len(games) > 6:
                game_blurb += f" (+{len(games)-6} more)"
            lines.append(
                f"- {r.path.name}: quality={quality}, {len(games)} games, "
                f"{r.latency_ms}ms, {r.input_tokens}in/{r.output_tokens}out tok"
            )
            if game_blurb:
                lines.append(f"    {game_blurb}")
        else:
            lines.append(f"- {r.path.name}: ERROR — {r.error}")
    lines += [
        "",
        f"Photos: {len(results)}  usable: {usable}  games-extracted: {total_games}",
        f"Tokens: {total_in} in / {total_out} out / {total_cache} cache-read",
    ]
    # Sonnet 4.6 ~$3/MTok in, $15/MTok out (uncached). Rough cost estimate:
    cost = (total_in - total_cache) * 3 / 1_000_000 + total_cache * 0.3 / 1_000_000 + total_out * 15 / 1_000_000
    lines.append(f"Est cost this run (Sonnet 4.6): ${cost:.4f}")
    return "\n".join(lines)


def main() -> int:
    photos = sorted(
        p for p in PHOTOS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ) if PHOTOS_DIR.exists() else []

    if not photos:
        print(f"No photos found in {PHOTOS_DIR}. Drop some JPG/PNG files in and rerun.")
        return 1

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set in env.")
        return 2

    model = os.getenv("VISION_TEST_MODEL", MODEL_BASELINE)
    client = anthropic.Anthropic()

    print(f"Running {len(photos)} photo(s) through {model} ...")
    results: list[PhotoResult] = []
    for p in photos:
        print(f"  -> {p.name}")
        results.append(run_one(client, p, model))

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"run-{stamp}.json"
    latest_path = RESULTS_DIR / "latest.json"
    payload = {
        "model": model,
        "ran_at": stamp,
        "results": [
            {
                "photo": r.path.name,
                "latency_ms": r.latency_ms,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "parsed": r.parsed,
                "raw_text": r.raw_text if not r.parsed else None,
                "error": r.error,
            }
            for r in results
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    latest_path.write_text(json.dumps(payload, indent=2))

    print(summarize(results))
    print(f"\nFull JSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
