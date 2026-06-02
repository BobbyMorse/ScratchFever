"""CI gate: if frontend/js/app.js changed on this branch, frontend/index.html
must also bump the `app.js?v=N` cache-buster. The pre-commit hook in
.githooks/pre-commit does this automatically; this script is the safety net
when the hook is bypassed or not installed."""
from __future__ import annotations
import re
import subprocess
import sys


APP_JS = "frontend/js/app.js"
INDEX = "frontend/index.html"
BASE = "origin/main"


def changed_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{BASE}...HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def index_diff() -> str:
    return subprocess.run(
        ["git", "diff", f"{BASE}...HEAD", "--", INDEX],
        capture_output=True, text=True, check=True,
    ).stdout


def main() -> int:
    files = changed_files()
    if APP_JS not in files:
        print(f"{APP_JS} unchanged; cache-buster check skipped.")
        return 0

    if INDEX not in files:
        print(
            f"ERROR: {APP_JS} changed but {INDEX} was not touched.\n"
            f"The app.js?v=N cache-buster must be bumped so browsers fetch the new file.\n"
            f"Install the local hook once with:\n"
            f"    git config core.hooksPath .githooks"
        )
        return 1

    diff = index_diff()
    added_v = re.findall(r"^\+.*app\.js\?v=(\d+)", diff, re.M)
    removed_v = re.findall(r"^-.*app\.js\?v=(\d+)", diff, re.M)
    if not added_v or not removed_v:
        print(
            f"ERROR: {INDEX} changed but no app.js?v=N line was added/removed.\n"
            f"Expected the cache-buster to be bumped."
        )
        return 1
    if added_v == removed_v:
        print(
            f"ERROR: app.js?v={added_v[0]} unchanged in {INDEX}; bump it."
        )
        return 1

    print(f"Cache-buster bumped: v{removed_v[0]} -> v{added_v[0]}. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
