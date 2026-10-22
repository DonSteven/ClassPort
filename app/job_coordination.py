"""Restricted task-table coordination, independently restartable from the API."""
import argparse
import asyncio
import logging
import json
from pathlib import Path
import signal

from . import db
from .job_config import REDELIVERY_SECONDS
from .logging_config import configure_logging, request_id
from .services.jobs import _retry_or_fail

log = logging.getLogger(__name__)


async def dispatch_once(publish=None) -> int:
    if publish is None:
        from .celery_app import publish
    sent = 0
    for _ in range(20):
        async with db.db() as conn, conn.transaction():
            row = await (await conn.execute(
                """SELECT o.*, j.status AS job_status, j.request_id,
                          j.next_run_at <= clock_timestamp() AS due
                   FROM outbox o JOIN jobs j ON j.user_id = o.user_id AND j.id = o.job_id
                   WHERE o.status = 'pending' AND o.available_at <= clock_timestamp()
                   ORDER BY o.available_at, o.id LIMIT 1 FOR UPDATE OF o SKIP LOCKED""",
            )).fetchone()
            if row is None:
                break
            if row["job_status"] in ("queued", "retry_wait") and row["due"]:
                await asyncio.to_thread(publish, row["job_id"])
            await conn.execute(
                """UPDATE outbox SET status = 'sent', sent_at = clock_timestamp()
                   WHERE user_id = %s AND id = %s""", (row["user_id"], row["id"]),
            )
            sent += 1
            token = request_id.set(row["request_id"])
            try:
                log.info("job.dispatched", extra={"event": "job.dispatched", "job_id": row["job_id"],
                                                 "user_id": row["user_id"]})
            finally:
                request_id.reset(token)
    return sent


async def recover_once(*, restored=False) -> dict:
    async with db.db() as conn, conn.transaction():
        if restored:
            # Run only in the isolated restore target, before consumers start.
            await conn.execute(
                "UPDATE jobs SET lease_until = clock_timestamp() - interval '1 second' WHERE status = 'running'",
            )
            await conn.execute(
                "UPDATE outbox SET status = 'sent', sent_at = clock_timestamp() WHERE status = 'pending'",
            )
        expired = await (await conn.execute(
            """SELECT * FROM jobs WHERE status = 'running' AND lease_until <= clock_timestamp()
               ORDER BY lease_until LIMIT 100 FOR UPDATE SKIP LOCKED""",
        )).fetchall()
        for job in expired:
            await _retry_or_fail(conn, job, "worker_lost", "Worker execution was interrupted",
                                 retryable=True, expired=True)
        cursor = await conn.execute(
            """INSERT INTO outbox(user_id, job_id)
               SELECT j.user_id, j.id FROM jobs j
               WHERE j.status IN ('queued', 'retry_wait') AND j.next_run_at <= clock_timestamp()
                 AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.user_id = j.user_id
                                 AND o.job_id = j.id AND o.status = 'pending')
                 AND (%s OR NOT EXISTS (SELECT 1 FROM outbox o WHERE o.user_id = j.user_id
                       AND o.job_id = j.id AND o.created_at > clock_timestamp() - %s * interval '1 second'))
               FOR UPDATE OF j SKIP LOCKED""", (restored, REDELIVERY_SECONDS),
        )
        return {"expired": len(expired), "redispatched": cursor.rowcount}


async def metrics() -> dict:
    return await db.fetchrow(
        """SELECT
          coalesce((SELECT extract(epoch FROM clock_timestamp()-min(created_at)) FROM jobs
            WHERE status IN ('queued','retry_wait') AND next_run_at <= clock_timestamp()),0)::float
            AS "JobWaitingAgeSeconds",
          coalesce((SELECT extract(epoch FROM clock_timestamp()-min(available_at)) FROM outbox
            WHERE status='pending' AND available_at <= clock_timestamp()),0)::float AS "JobOutboxAgeSeconds",
          (SELECT count(*) FROM jobs WHERE status='running' AND lease_until <= clock_timestamp())
            AS "JobExpiredLeases",
          coalesce((SELECT avg(extract(epoch FROM finished_at-started_at)) FROM job_attempts
            WHERE finished_at > clock_timestamp()-interval '15 minutes'),0)::float AS "JobProcessingSeconds",
          coalesce((SELECT 100.0*count(*) FILTER (WHERE status='failed')/nullif(count(*),0) FROM jobs
            WHERE status IN ('succeeded','failed') AND finished_at > clock_timestamp()-interval '15 minutes'),0)::float
            AS "JobFailureRate",
          coalesce((SELECT sum(greatest(attempt_count-1,0)) FROM jobs
            WHERE updated_at > clock_timestamp()-interval '15 minutes'),0)::float AS "JobRetries"
        """,
    )


async def main(mode, once=False):
    await db.init_pool()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set():
            try:
                if mode == "dispatch":
                    await dispatch_once()
                elif mode == "metrics":
                    print(json.dumps(await metrics()), flush=True)
                else:
                    await recover_once(restored=mode == "restore")
                Path(f"/tmp/classport-{mode}.heartbeat").touch()
            except Exception:
                log.exception("coordination.failed", extra={"event": "coordination.failed", "stage": mode})
                if once:
                    raise
            if once:
                break
            try:
                await asyncio.wait_for(stop.wait(), 2 if mode == "dispatch" else 5)
            except TimeoutError:
                pass
    finally:
        await db.close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["dispatch", "recover", "restore", "metrics"])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.mode in ("restore", "metrics") and not args.once:
        parser.error("restore/metrics requires --once; restore also requires stopped background consumers")
    configure_logging()
    asyncio.run(main(args.mode, args.once))
