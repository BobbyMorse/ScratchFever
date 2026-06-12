"""
Create (or update) the Stripe webhook endpoint that feeds
backend/billing_api.py. Run once per Stripe environment (test/live).

The signing secret is only returned at creation. If an endpoint at the
same URL already exists this script updates its event list but cannot
re-read the secret — delete the old endpoint in the Stripe dashboard
and re-run if you need a fresh secret.

Usage:
    python scripts/setup_stripe_webhook.py
    python scripts/setup_stripe_webhook.py --url https://scratchfever.app/api/stripe/webhook
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import stripe

DEFAULT_URL = "https://scratchfever.app/api/stripe/webhook"

# Must mirror the event types handled in backend/billing_api.py
EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.resumed",
    "customer.subscription.paused",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("STRIPE_WEBHOOK_URL", DEFAULT_URL))
    args = ap.parse_args()

    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        print("ERROR: STRIPE_SECRET_KEY not set", file=sys.stderr)
        return 1
    stripe.api_key = key
    mode = "TEST" if key.startswith("sk_test_") else "LIVE"
    print(f"Stripe mode: {mode}")
    print(f"Webhook URL: {args.url}")

    existing = None
    for ep in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter():
        if ep.url == args.url and ep.status == "enabled":
            existing = ep
            break

    if existing:
        ep = stripe.WebhookEndpoint.modify(existing.id, enabled_events=EVENTS)
        print(f"Updated existing endpoint: {ep.id}")
        print("Signing secret is only readable on creation — keep your saved value.")
        return 0

    ep = stripe.WebhookEndpoint.create(
        url=args.url,
        enabled_events=EVENTS,
        description="ScratchFrenzy Pro — Stripe billing webhook",
    )
    print(f"Created: {ep.id}")
    print()
    print("Paste this into .env:")
    print(f"STRIPE_WEBHOOK_SECRET={ep.secret}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
