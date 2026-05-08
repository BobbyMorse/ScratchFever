"""
Bland AI backend for the caller system.

Bland handles the entire call lifecycle: telephony, STT, LLM, TTS.
We just POST a task prompt + phone number and they call us back with the transcript.

Docs: https://docs.bland.ai
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

BLAND_API_URL = "https://api.bland.ai/v1/calls"


def build_task(game_info: dict) -> str:
    name   = game_info.get("game_name", "")
    number = game_info.get("game_number", "")
    price  = float(game_info.get("game_price", 0))

    price_str   = f"${price:.0f}" if price else "a"
    number_part = f", game number {number}" if number else ""

    return f"""\
You are doing a quick automated inventory check call for a Massachusetts Lottery scratch ticket. \
Follow this exact conversation flow and keep every response to one short sentence:

1. Open with: "Hi, this is an automated inventory check — do you carry Mass scratch tickets?"
2. If YES: say "Ok nice — do you have the {name} in stock? It's the {price_str} ticket{number_part}."
3. If they ask for more info or don't recognize it: say "It's called {name}, {price_str}{number_part}."
4. If they HAVE it: say "Awesome, thanks so much!" then end the call.
5. If they do NOT have it: say "No problem, thanks anyway!" then end the call.
6. If they are unsure or need to check: wait politely, then ask "Any luck?"

Speak slowly and naturally. One sentence per response. End the call as soon as you have a clear answer."""


async def initiate_call(phone_e164: str, game_info: dict,
                        webhook_url: str, metadata: dict) -> str:
    """
    Start a Bland AI outbound call.
    Returns the Bland call_id on success, raises on failure.
    """
    api_key = os.getenv("BLAND_API_KEY")
    if not api_key:
        raise RuntimeError("BLAND_API_KEY not set")

    voice = os.getenv("BLAND_VOICE", "matt")

    payload = {
        "phone_number": phone_e164,
        "task": build_task(game_info),
        "voice": voice,
        "model": "enhanced",
        "reduce_latency": True,
        "interruption_threshold": 50,
        "max_duration": 5,
        "wait_for_greeting": True,
        "answered_by_enabled": True,
        "voicemail_action": "hangup",
        "webhook": webhook_url,
        "metadata": metadata,
        "language": "en-US",
    }

    from_number = os.getenv("BLAND_FROM_NUMBER")
    if from_number:
        payload["from"] = from_number

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            BLAND_API_URL,
            json=payload,
            headers={"Authorization": api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    call_id = data.get("call_id") or data.get("id")
    if not call_id:
        raise RuntimeError(f"Bland returned no call_id: {data}")

    logger.info("Bland call initiated: call_id=%s to=%s game=%s",
                call_id, phone_e164, game_info.get("game_name"))
    return call_id
