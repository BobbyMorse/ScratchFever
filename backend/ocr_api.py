from __future__ import annotations
import json
import os
import re
from difflib import SequenceMatcher
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.database import get_pool
from backend.users import require_member

router = APIRouter()

_OCR_PROMPT = """\
Analyze this lottery scratch-off ticket image and return ONLY valid JSON — no other text — \
matching this schema exactly:
{
  "rawText": "all text visible on the ticket",
  "gameName": "game title from the ticket or null",
  "ticketNumber": "serial/ticket number if visible or null",
  "gameType": "number_match | bingo | instant_win | unknown",
  "winningNumbers": [integers],
  "yourNumbers": [integers — the player's revealed numbers],
  "callerNumbers": [integers — for bingo: drawn/called numbers],
  "bingoCard": [integers — numbers printed on the bingo card],
  "autoWinSymbol": true or false,
  "multiplier": integer or null,
  "multiplierPrize": number or null,
  "bonusWins": [prize amounts as numbers],
  "won": true / false / null,
  "winReason": "brief explanation or null",
  "prizeHint": number or null
}
gameType rules:
- number_match: ticket has winning numbers and player numbers to compare
- bingo: bingo card with caller/drawn numbers
- instant_win: prizes revealed directly (no number comparison)
Set won=null when you cannot confidently determine the result."""


def _fuzzy(a: str, b: str) -> float:
    a = re.sub(r"[^a-z0-9 ]", "", a.lower())
    b = re.sub(r"[^a-z0-9 ]", "", b.lower())
    return SequenceMatcher(None, a, b).ratio()


class ScanTicketBody(BaseModel):
    image: str  # base64-encoded image, with or without data-URI prefix


@router.post("/api/scan-ticket")
async def scan_ticket(body: ScanTicketBody, user: dict = Depends(require_member)):
    api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OCR service not configured")

    raw = body.image
    mime_type = "image/jpeg"
    if raw.startswith("data:"):
        header, raw = raw.split(",", 1)
        if "png" in header:
            mime_type = "image/png"
        elif "webp" in header:
            mime_type = "image/webp"
        elif "gif" in header:
            mime_type = "image/gif"

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(data=__import__("base64").b64decode(raw), mime_type=mime_type),
                _OCR_PROMPT,
            ],
        )
        response_text = response.text
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Vision API error: {exc}")

    json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if not json_match:
        raise HTTPException(status_code=422, detail="Could not parse OCR response")

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Invalid JSON from OCR")

    def _ints(key: str) -> list[int]:
        return [int(n) for n in data.get(key, []) if n is not None]

    winning = _ints("winningNumbers")
    yours = _ints("yourNumbers")
    matched = [n for n in yours if n in winning]

    scanned_name: Optional[str] = data.get("gameName")
    matched_game = None
    matched_game_score = 0.0

    if scanned_name:
        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, state_code, state_name, game_id, name, price, ev, return_pct,"
                " overall_odds_one_in, top_prize, top_prize_remaining, total_tickets,"
                " tickets_remaining, is_active, image_url, scraped_at"
                " FROM games WHERE is_active=TRUE"
            )
        best_score = 0.0
        best_row = None
        for row in rows:
            score = _fuzzy(scanned_name, row["name"])
            if score > best_score:
                best_score = score
                best_row = row

        if best_row and best_score >= 0.5:
            matched_game = dict(best_row)
            if matched_game.get("scraped_at"):
                matched_game["scraped_at"] = matched_game["scraped_at"].isoformat()
            matched_game_score = best_score

    return {
        "rawText": data.get("rawText", response_text),
        "scannedGameName": scanned_name,
        "matchedGame": matched_game,
        "matchedGameScore": matched_game_score,
        "ticketNumber": data.get("ticketNumber"),
        "analysis": {
            "gameType": data.get("gameType", "unknown"),
            "winningNumbers": winning,
            "yourNumbers": yours,
            "matchedNumbers": matched,
            "matchedPrize": data.get("matchedPrize"),
            "multiplier": data.get("multiplier"),
            "multiplierPrize": data.get("multiplierPrize"),
            "callerNumbers": _ints("callerNumbers"),
            "bingoCard": _ints("bingoCard"),
            "autoWinSymbol": bool(data.get("autoWinSymbol", False)),
            "bonusWins": [float(n) for n in data.get("bonusWins", []) if n is not None],
            "won": data.get("won"),
            "winReason": data.get("winReason"),
            "prizeHint": data.get("prizeHint"),
        },
    }
