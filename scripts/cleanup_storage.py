"""Plan cleanup, inspect failures or retry a failed cleanup for one owner.

--run executes due cleanup jobs. Mount the original STUDY_ROOT when using
--local-sources after the migration rollback window.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db  # noqa: E402
from app.services import jobs, storage_cleanup  # noqa: E402


async def main(args):
    await db.init_pool()
    try:
        if args.retry:
            async with db.db() as conn, conn.transaction():
                row = await (await conn.execute(
                    """UPDATE jobs SET status='queued',next_run_at=clock_timestamp(),finished_at=NULL,
                       execution_id=NULL,lease_until=NULL,error_code=NULL,last_error=NULL
                       WHERE user_id=%s AND id=%s AND job_type='storage_cleanup' AND status='failed'
                       RETURNING id""", (args.user_id, args.retry),
                )).fetchone()
                if row is None:
                    raise ValueError('failed cleanup job not found for this owner')
                await conn.execute('INSERT INTO outbox(user_id,job_id) VALUES (%s,%s)', (args.user_id, row['id']))
        scanned = await storage_cleanup.scan_user(args.user_id, limit=args.limit, local_sources=args.local_sources)
        if args.run:
            rows = await db.fetch(
                """SELECT id FROM jobs WHERE user_id=%s AND job_type='storage_cleanup'
                   AND status IN ('queued','retry_wait') AND next_run_at<=clock_timestamp() LIMIT %s""",
                args.user_id, args.limit,
            )
            for row in rows:
                await jobs.run(row['id'], 'storage-maintenance-command')
        states = await db.fetch(
            """SELECT id,status,error_code,last_error FROM jobs
               WHERE user_id=%s AND job_type='storage_cleanup' ORDER BY created_at""", args.user_id,
        )
        return {'scanned': scanned, 'jobs': states}
    finally:
        await db.close_pool()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', type=UUID, required=True)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--local-sources', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--retry', type=UUID)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    result = asyncio.run(main(args))
    print(json.dumps(result))
    raise SystemExit(1 if args.run and any(job['status'] == 'failed' for job in result['jobs']) else 0)
