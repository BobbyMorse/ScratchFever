"""
Call runner: picks stores from the queue and initiates Twilio outbound calls
during MA business hours (9 AM – 8 PM ET, Monday–Saturday).

Usage (in main.py lifespan):
    runner = CallRunner()
    runner.attach_scheduler(scheduler)
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.caller.db import get_campaign, get_next_pending, mark_calling

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MAX_CONCURRENT_CALLS = int(os.getenv("CALLER_MAX_CONCURRENT", "3"))
SECONDS_BETWEEN_CALLS = float(os.getenv("CALLER_CALL_INTERVAL_SEC", "4"))


def is_business_hours() -> bool:
    now = datetime.now(ET)
    # Mon=0 … Sat=5; skip Sunday (6)
    if now.weekday() == 6:
        return False
    return 9 <= now.hour < 20


class CallRunner:
    def __init__(self):
        self.active_campaigns: set[int] = set()
        self._in_flight: set[str] = set()   # call SIDs currently active
        self._scheduler = None

    def attach_scheduler(self, scheduler):
        self._scheduler = scheduler
        scheduler.add_job(
            self._tick,
            "interval",
            seconds=30,
            id="caller_tick",
            replace_existing=True,
        )
        logger.info("CallRunner attached to scheduler")

    def start_campaign(self, campaign_id: int):
        self.active_campaigns.add(campaign_id)
        logger.info("Campaign %d started", campaign_id)

    def stop_campaign(self, campaign_id: int):
        self.active_campaigns.discard(campaign_id)
        logger.info("Campaign %d paused", campaign_id)

    def is_running(self) -> bool:
        return bool(self.active_campaigns)

    def calls_in_flight(self) -> int:
        return len(self._in_flight)

    def call_completed(self, call_sid: str):
        self._in_flight.discard(call_sid)

    async def _tick(self):
        if not is_business_hours():
            return
        if not self.active_campaigns:
            return
        if len(self._in_flight) >= MAX_CONCURRENT_CALLS:
            return

        for campaign_id in list(self.active_campaigns):
            slots = MAX_CONCURRENT_CALLS - len(self._in_flight)
            if slots <= 0:
                break

            campaign = await get_campaign(campaign_id)
            if not campaign or campaign["status"] != "active":
                self.active_campaigns.discard(campaign_id)
                continue

            queue_item = await get_next_pending(campaign_id)
            if not queue_item:
                logger.info("Campaign %d: queue exhausted, stopping", campaign_id)
                self.active_campaigns.discard(campaign_id)
                continue

            call_sid = await self._initiate_call(queue_item, campaign)
            if call_sid:
                self._in_flight.add(call_sid)
                await asyncio.sleep(SECONDS_BETWEEN_CALLS)

    async def _initiate_call(self, queue_item: dict, campaign: dict) -> str | None:
        to_number = queue_item.get("phone_e164")
        if not to_number:
            logger.warning("Queue item %d has no E.164 phone", queue_item["id"])
            return None

        backend = campaign.get("call_backend", "twilio_ivr")
        if backend == "twilio_ivr":
            return await self._initiate_ivr_call(queue_item, campaign, to_number)
        return await self._initiate_twilio_call(queue_item, campaign, to_number)

    async def _initiate_ivr_call(self, queue_item: dict, campaign: dict,
                                  to_number: str) -> str | None:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
        from_number = os.getenv("TWILIO_PHONE_NUMBER")
        base_url    = os.getenv("CALLER_BASE_URL", "").rstrip("/")

        if not all([account_sid, auth_token, from_number]):
            logger.error("Twilio IVR: missing credentials (TWILIO_ACCOUNT_SID/AUTH_TOKEN/PHONE_NUMBER)")
            return None
        if not base_url:
            logger.error("Twilio IVR: CALLER_BASE_URL not set")
            return None

        queue_id   = queue_item["id"]
        twiml_url  = f"{base_url}/caller/ivr/twiml/{queue_id}"
        status_url = f"{base_url}/caller/ivr/status/{queue_id}"

        try:
            from twilio.rest import Client as TwilioClient
            from backend.caller.webhook import register_call
            register_call(queue_id, campaign, queue_item)

            client = TwilioClient(account_sid, auth_token)
            call = client.calls.create(
                to=to_number,
                from_=from_number,
                url=twiml_url,
                status_callback=status_url,
                status_callback_method="POST",
                status_callback_event=["completed", "no-answer", "busy", "failed"],
                machine_detection="Enable",
                machine_detection_timeout=4,
                timeout=25,
            )
            await mark_calling(queue_id, call.sid)
            logger.info("Twilio IVR call to %s (%s) SID=%s",
                        queue_item["name"], to_number, call.sid)
            return call.sid
        except Exception as exc:
            logger.error("Twilio IVR call failed for %s: %s", queue_item["name"], exc)
            from backend.caller.db import update_queue_status
            await update_queue_status(queue_id, "failed")
            return None

    async def _initiate_twilio_call(self, queue_item: dict, campaign: dict,
                                     to_number: str) -> str | None:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
        from_number = os.getenv("TWILIO_PHONE_NUMBER")
        base_url    = os.getenv("CALLER_BASE_URL", "").rstrip("/")

        if not all([account_sid, auth_token, from_number, base_url]):
            logger.error("Missing Twilio env vars (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
                         "TWILIO_PHONE_NUMBER, CALLER_BASE_URL)")
            return None

        queue_id   = queue_item["id"]
        twiml_url  = f"{base_url}/caller/twiml/{queue_id}"
        status_url = f"{base_url}/caller/status/{queue_id}"

        try:
            from twilio.rest import Client as TwilioClient
            from backend.caller.webhook import register_call
            register_call(queue_id, campaign, queue_item)

            client = TwilioClient(account_sid, auth_token)
            call = client.calls.create(
                to=to_number,
                from_=from_number,
                url=twiml_url,
                status_callback=status_url,
                status_callback_event=["completed", "no-answer", "busy", "failed"],
                machine_detection="Enable",
                machine_detection_timeout=4,
                timeout=20,
            )
            await mark_calling(queue_id, call.sid)
            logger.info("Twilio call to %s (%s) SID=%s",
                        queue_item["name"], to_number, call.sid)
            return call.sid

        except Exception as exc:
            logger.error("Twilio call failed for %s: %s", queue_item["name"], exc)
            from backend.caller.db import update_queue_status
            await update_queue_status(queue_id, "failed")
            return None
