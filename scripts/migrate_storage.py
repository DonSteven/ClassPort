"""Copy and verify one user's registered local versions into S3. Retains sources."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db  # noqa: E402
from app.services.storage_migration import migrate_user  # noqa: E402


async def main(args):
    await db.init_pool()
    try:
        return await migrate_user(args.user_id, limit=args.limit)
    finally:
        await db.close_pool()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', type=UUID, required=True)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    report = asyncio.run(main(args))
    print(json.dumps(report))
    raise SystemExit(1 if report['errors'] else 0)
