"""
CLI for the ScratchFever caller system.

Usage:
  python caller.py create --game "300X" --number 1234 --price 30
  python caller.py create --game "300X" --number 1234 --price 30 --tiers ELITE --max 100
  python caller.py start  --campaign 1
  python caller.py pause  --campaign 1
  python caller.py status --campaign 1
  python caller.py hits
  python caller.py hits   --campaign 1
"""
import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from backend.caller.db import (
    create_campaign, get_campaign, get_hits, get_queue_stats,
    get_recent_results, init_caller_db, list_campaigns, populate_queue,
)
from backend.database import init_db


async def cmd_create(args):
    await init_db()
    await init_caller_db()
    tiers = [t.strip().upper() for t in args.tiers.split(",")]
    campaign_id = await create_campaign(
        game_name=args.game,
        game_number=args.number,
        game_price=args.price,
        target_tiers=",".join(tiers),
        max_stores=args.max,
    )
    inserted = await populate_queue(campaign_id, tiers, args.max)
    print(f"Campaign #{campaign_id} created.")
    print(f"  Game:   {args.game}  (#{args.number}, ${args.price:.0f})")
    print(f"  Tiers:  {', '.join(tiers)}")
    print(f"  Stores: {inserted} queued (max {args.max})")
    print()
    print(f"To start calling:")
    print(f"  python caller.py start --campaign {campaign_id}")
    print()
    print("Make sure these env vars are set in .env:")
    print("  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER")
    print("  ANTHROPIC_API_KEY")
    print("  CALLER_BASE_URL  (public URL where Twilio can reach this server)")


async def cmd_start(args):
    print(f"Campaigns are started via the API: POST /api/caller/campaigns/{args.campaign}/start")
    print("Or run the server (python run.py) and call the endpoint.")


async def cmd_status(args):
    await init_db()
    await init_caller_db()
    if args.campaign:
        campaign = await get_campaign(args.campaign)
        if not campaign:
            print(f"Campaign #{args.campaign} not found.")
            sys.exit(1)
        stats = await get_queue_stats(args.campaign)
        recent = await get_recent_results(args.campaign, limit=10)
        print(f"\n{'='*50}")
        print(f"Campaign #{campaign['id']}: {campaign['game_name']}")
        print(f"  Game #:   {campaign['game_number']}  ${campaign['game_price']:.0f}")
        print(f"  Status:   {campaign['status']}")
        print(f"  Calls:    {campaign['calls_made']} made, {campaign['hits_found']} hits")
        print(f"  Tiers:    {campaign['target_tiers']}  (max {campaign['max_stores']} stores)")
        print(f"\nQueue breakdown:")
        for status, count in sorted(stats.items()):
            print(f"  {status:12s}  {count}")
        if recent:
            print(f"\nRecent results (last {len(recent)}):")
            for r in recent:
                flag = "✓ YES" if r["has_game"] == 1 else ("✗ NO " if r["has_game"] == 0 else "?   ")
                conf = f"{r['confidence']:.0%}" if r["confidence"] else "  "
                print(f"  {flag} ({conf})  {r['name']}  —  {r['city']}")
    else:
        campaigns = await list_campaigns()
        if not campaigns:
            print("No campaigns found. Create one with: python caller.py create --game ...")
            return
        print(f"\n{'#':>4}  {'Game':25}  {'Calls':>6}  {'Hits':>5}  Status")
        print("-" * 60)
        for c in campaigns:
            print(f"  {c['id']:>2}  {c['game_name']:25}  {c['calls_made']:>6}  {c['hits_found']:>5}  {c['status']}")


async def cmd_hits(args):
    await init_db()
    await init_caller_db()
    hits = await get_hits(campaign_id=args.campaign)
    if not hits:
        print("No positive hits found yet.")
        return
    header = f"\n{'Store':30}  {'City':15}  {'Phone':15}  {'Conf':>6}  Notes"
    print(header)
    print("-" * 90)
    for h in hits:
        conf = f"{h['confidence']:.0%}" if h.get("confidence") else "  "
        notes = (h.get("notes") or "")[:40]
        name = (h.get("name") or "")[:29]
        city = (h.get("city") or "")[:14]
        phone = (h.get("phone") or "")[:14]
        print(f"  {name:30}  {city:15}  {phone:15}  {conf:>6}  {notes}")


def main():
    parser = argparse.ArgumentParser(prog="caller", description="ScratchFever call agent CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_create = sub.add_parser("create", help="Create a new calling campaign")
    p_create.add_argument("--game", required=True, help="Scratch ticket name (e.g. '300X')")
    p_create.add_argument("--number", default="", help="Game number from MA Lottery")
    p_create.add_argument("--price", type=float, default=0.0, help="Ticket price (e.g. 30)")
    p_create.add_argument("--tiers", default="ELITE,GOOD", help="Comma-separated tiers to call")
    p_create.add_argument("--max", type=int, default=200, help="Max stores to queue")

    p_start = sub.add_parser("start", help="Start a campaign (via API)")
    p_start.add_argument("--campaign", type=int, required=True)

    p_status = sub.add_parser("status", help="Show campaign(s) status")
    p_status.add_argument("--campaign", type=int, default=None)

    p_hits = sub.add_parser("hits", help="Show positive inventory hits")
    p_hits.add_argument("--campaign", type=int, default=None)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "create": cmd_create,
        "start": cmd_start,
        "status": cmd_status,
        "hits": cmd_hits,
    }
    asyncio.run(dispatch[args.cmd](args))


if __name__ == "__main__":
    main()
