"""One-off: promote bobby@evqagentic.com to admin, demote robertmorse73@gmail.com to member."""
import asyncio, os, sys
import asyncpg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

PROMOTE = "bobby@evqagentic.com"
DEMOTE = "robertmorse73@gmail.com"


async def main() -> int:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            for email, new_role in [(PROMOTE, "admin"), (DEMOTE, "member")]:
                row = await conn.fetchrow(
                    "SELECT id, email, username, role FROM users WHERE email=$1",
                    email.lower().strip(),
                )
                if not row:
                    print(f"Missing user: {email}", file=sys.stderr)
                    return 2
                print(f"Before: id={row['id']} email={row['email']} role={row['role']}")
                await conn.execute(
                    "UPDATE users SET role=$1 WHERE id=$2",
                    new_role, row["id"],
                )
                after = await conn.fetchrow(
                    "SELECT id, email, role FROM users WHERE id=$1", row["id"],
                )
                print(f"After:  id={after['id']} email={after['email']} role={after['role']}")
                print()
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
