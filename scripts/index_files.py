"""Operator command to index completed database file versions.

Run with DATABASE_URL and STUDY_ROOT in the environment:
    uv run --no-sync python scripts/index_files.py --user-id USER_UUID
    uv run --no-sync python scripts/index_files.py --all-users

Unchanged immutable versions are skipped. Legacy files must be imported first.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

# Allow running from anywhere by putting the repo root on sys.path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.logging_config import configure_logging  # noqa: E402

from app import db  # noqa: E402
from app.services.file_index import index_user  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--user-id", type=UUID, help="Rebuild only this user's current versions")
    scope.add_argument("--all-users", action="store_true", help="Explicit operator-wide rebuild")
    return parser.parse_args(argv)


async def _main(args) -> dict:
    await db.init_pool()
    try:
        if args.user_id is not None:
            return await index_user(args.user_id)
        rows = await db.fetch("SELECT DISTINCT user_id FROM files ORDER BY user_id")
        reports = []
        for row in rows:
            reports.append({"user_id": row["user_id"], **await index_user(row["user_id"])})
        return {"failed": sum(report["failed"] for report in reports), "users": reports}
    finally:
        await db.close_pool()


if __name__ == "__main__":
    configure_logging()
    stats = asyncio.run(_main(parse_args()))
    print(json.dumps(stats, ensure_ascii=False))
    raise SystemExit(1 if stats["failed"] else 0)
