"""
One-shot helper: creates the ScratchFrenzy Pro product + monthly/yearly prices
in Stripe (idempotent — looks up existing entries by lookup_key first), then
prints the price IDs to paste into .env as STRIPE_PRICE_MONTHLY /
STRIPE_PRICE_YEARLY.

Run from project root:
    python scripts/setup_stripe_products.py
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# Load .env from project root regardless of cwd.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import stripe

PRODUCT_NAME = "ScratchFrenzy Pro"
PRODUCT_DESCRIPTION = "Premium EV strategies · The Chase (store-level data) · Ticket upvotes"

PLANS = [
    {
        "lookup_key": "scratchfrenzy_pro_monthly_v2",
        "amount_cents": 799,   # $7.99
        "interval": "month",
        "label": "Monthly",
        "env": "STRIPE_PRICE_MONTHLY",
    },
    {
        "lookup_key": "scratchfrenzy_pro_yearly_v2",
        "amount_cents": 7188,  # $71.88 = $5.99/mo (~25% off monthly)
        "interval": "year",
        "label": "Yearly",
        "env": "STRIPE_PRICE_YEARLY",
    },
]


def find_or_create_product() -> str:
    existing = stripe.Product.list(limit=100)
    for p in existing.auto_paging_iter():
        if p.name == PRODUCT_NAME and p.active:
            return p.id
    p = stripe.Product.create(name=PRODUCT_NAME, description=PRODUCT_DESCRIPTION)
    return p.id


def find_or_create_price(product_id: str, plan: dict) -> str:
    existing = stripe.Price.list(lookup_keys=[plan["lookup_key"]], limit=10)
    for pr in existing.data:
        if pr.active:
            return pr.id
    pr = stripe.Price.create(
        product=product_id,
        unit_amount=plan["amount_cents"],
        currency="usd",
        recurring={"interval": plan["interval"]},
        lookup_key=plan["lookup_key"],
        nickname=f"{PRODUCT_NAME} — {plan['label']}",
    )
    return pr.id


def main() -> int:
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        print("ERROR: STRIPE_SECRET_KEY not set in .env", file=sys.stderr)
        return 1
    stripe.api_key = key

    mode = "TEST" if key.startswith("sk_test_") else "LIVE"
    print(f"Stripe mode: {mode}")

    product_id = find_or_create_product()
    print(f"Product: {product_id} ({PRODUCT_NAME})")

    env_lines: list[str] = []
    for plan in PLANS:
        price_id = find_or_create_price(product_id, plan)
        print(f"  {plan['label']:<8} -> {price_id}  (${plan['amount_cents'] / 100:.2f}/{plan['interval']})")
        env_lines.append(f"{plan['env']}={price_id}")

    print("\nPaste these into .env:\n")
    for line in env_lines:
        print(line)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
