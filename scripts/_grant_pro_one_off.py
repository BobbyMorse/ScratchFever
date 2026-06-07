"""One-off: grant Pro + rename to a specific user. Delete after running."""
import asyncio, os, sys, datetime as dt
import asyncpg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

EMAIL = "bobby@evqagentic.com"
NEW_USERNAME = "bobbyEVQ"
PRO_YEARS = 100


async def main() -> int:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id, email, username, role, pro_until FROM users WHERE email=$1",
            EMAIL.lower().strip(),
        )
        if not row:
            print(f"No user found for {EMAIL}", file=sys.stderr)
            return 2
        print(f"Before: id={row['id']} email={row['email']} username={row['username']!r} "
              f"role={row['role']} pro_until={row['pro_until']}")

        new_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365 * PRO_YEARS)
        try:
            await conn.execute(
                "UPDATE users SET username=$1, pro_until=$2 WHERE id=$3",
                NEW_USERNAME, new_until, row["id"],
            )
        except asyncpg.UniqueViolationError:
            print(f"Username {NEW_USERNAME!r} already taken — aborting", file=sys.stderr)
            return 3

        after = await conn.fetchrow(
            "SELECT id, email, username, role, pro_until FROM users WHERE id=$1",
            row["id"],
        )
        print(f"After:  id={after['id']} email={after['email']} username={after['username']!r} "
              f"role={after['role']} pro_until={after['pro_until']}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
