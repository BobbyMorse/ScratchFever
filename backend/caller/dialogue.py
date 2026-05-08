from __future__ import annotations
import json
import os
import re
from typing import AsyncGenerator

import anthropic

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


SYSTEM_PROMPT = """\
You are making a brief outbound call to a Massachusetts convenience store or lottery retailer \
to check if they have a specific scratch ticket in stock. You work for a lottery research service.

Target game:
- Name: {game_name}
- Price: ${game_price:.0f}
- Game number: {game_number}

How to handle the conversation:
- Keep every response to 1–2 short sentences. Clerks are busy.
- If they confirm YES: ask roughly how many are left (one quick follow-up), then thank them warmly and say goodbye.
- If they say NO: ask if they can still order it, then thank them and say goodbye.
- If they're unsure or don't recognize the name: try again using the game number and price point.
- Once you have the information (or after 3–4 turns), wrap up: "Thanks so much, have a great day!"
- Never repeat yourself more than once.
- Do NOT identify yourself as an AI.
"""

ENDING_PHRASES = (
    "have a great day",
    "have a good day",
    "goodbye",
    "good bye",
    "take care",
    "thanks so much",
    "thank you so much",
)


def build_system(game_info: dict) -> str:
    return SYSTEM_PROMPT.format(
        game_name=game_info.get("game_name", "unknown"),
        game_price=float(game_info.get("game_price", 0)),
        game_number=game_info.get("game_number", "unknown"),
    )


def opening_line(game_info: dict) -> str:
    name = game_info.get("game_name", "")
    price = float(game_info.get("game_price", 0))
    number = game_info.get("game_number", "")
    parts = [f"Hi! Quick inventory question —"]
    parts.append(f"do you currently have the Massachusetts Lottery scratch ticket called '{name}'?")
    if number:
        parts.append(f"It's the ${price:.0f} game, number {number}.")
    else:
        parts.append(f"It's the ${price:.0f} ticket.")
    return " ".join(parts)


async def stream_response(
    messages: list[dict], game_info: dict
) -> AsyncGenerator[str, None]:
    client = get_client()
    full_text = ""
    async with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=120,
        system=build_system(game_info),
        messages=messages,
    ) as stream:
        async for token in stream.text_stream:
            full_text += token
            yield token
    # store full text as final yield so callers can capture it
    yield "\x00" + full_text


async def extract_result(transcript: str, game_name: str) -> dict:
    client = get_client()
    msg = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Read this phone call transcript about the lottery ticket '{game_name}' "
                    f"and extract the inventory information.\n\nTranscript:\n{transcript}\n\n"
                    'Return only valid JSON: {"has_game": true/false/null, '
                    '"confidence": 0.0-1.0, "can_order": true/false/null, "notes": "..."}'
                ),
            }
        ],
    )
    text = msg.content[0].text
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"has_game": None, "confidence": 0.0, "can_order": None, "notes": "parse error"}


def should_end_call(assistant_text: str) -> bool:
    lower = assistant_text.lower()
    return any(phrase in lower for phrase in ENDING_PHRASES)


def format_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = "Caller" if m["role"] == "assistant" else "Store"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines)
