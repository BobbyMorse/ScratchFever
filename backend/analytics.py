"""
PostHog event capture, centralized.

No-ops when POSTHOG_API_KEY is unset, so dev/test/CI never ship events to
prod analytics. Set POSTHOG_API_KEY (and optionally POSTHOG_HOST for EU)
in Railway env to enable.

Use capture() everywhere — it swallows its own exceptions so analytics
failures never propagate into request handlers.
"""
from __future__ import annotations
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_client = None  # set by init(); None means analytics disabled


def init() -> None:
    """Call once at process startup. Idempotent."""
    global _client
    if _client is not None:
        return
    api_key = os.environ.get("POSTHOG_API_KEY", "").strip()
    if not api_key:
        logger.info("analytics: POSTHOG_API_KEY unset, capture() will no-op")
        return
    try:
        from posthog import Posthog
    except ImportError:
        logger.warning("analytics: posthog not installed; skipping init")
        return
    host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com").strip()
    _client = Posthog(api_key, host=host)
    logger.info("analytics: PostHog initialized (host=%s)", host)


def capture(distinct_id: str | int, event: str, properties: dict[str, Any] | None = None) -> None:
    """Fire an event. distinct_id should be a stable user id (or 'anon-<hash>'
    for prelogin events). Never raises."""
    if _client is None:
        return
    try:
        _client.capture(distinct_id=str(distinct_id), event=event, properties=properties or {})
    except Exception as exc:
        logger.debug("analytics: capture failed for %s: %s", event, exc)


def identify(distinct_id: str | int, properties: dict[str, Any] | None = None) -> None:
    """Attach user properties (email, plan, etc.) to a distinct_id."""
    if _client is None:
        return
    try:
        _client.identify(distinct_id=str(distinct_id), properties=properties or {})
    except Exception as exc:
        logger.debug("analytics: identify failed: %s", exc)


def shutdown() -> None:
    """Flush pending events before process exit."""
    if _client is None:
        return
    try:
        _client.shutdown()
    except Exception:
        pass
