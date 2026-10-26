"""PostgreSQL is the source of truth for file-processing work."""
from __future__ import annotations

import logging
from uuid import UUID, uuid4

from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from .. import db
from ..job_config import LEASE_SECONDS, MAX_ATTEMPTS
from ..logging_config import job_context, request_id
from ..storage.base import StorageConflict, StorageNotFound, StorageUnavailable

log = logging.getLogger(__name__)
ACTIVE = ("queued", "running", "retry_wait")
RETRYABLE = {"storage_unavailable", "database_unavailable", "worker_lost"}
LABELS = {"queued": "等待处理", "running": "处理中", "retry_wait": "等待重试",
          "succeeded": "处理成功", "failed": "处理失败", "cancelled": "已取消"}


class ExecutionExpired(Exception):
    """Abort the entire result transaction if the lease expires during a write."""


async def create(conn, user_id, file_id, version_id) -> dict:
    """Called inside the file completion transaction; never publishes to Redis."""
    from .file_index import PIPELINE_VERSION

    row = await (await conn.execute(
        """INSERT INTO jobs(user_id, file_id, file_version_id, job_type,
                            pipeline_version, request_id)
           VALUES (%s, %s, %s, 'index_file', %s, %s)
           ON CONFLICT (file_version_id, job_type, pipeline_version) WHERE cleanup_id IS NULL DO NOTHING
           RETURNING *""", (user_id, file_id, version_id, PIPELINE_VERSION, request_id.get()),
    )).fetchone()
    if row is not None:
        await conn.execute("INSERT INTO outbox(user_id, job_id) VALUES (%s, %s)",
                           (user_id, row["id"]))
        _log("job.created", row)
    else:
        row = await (await conn.execute(
            """SELECT * FROM jobs WHERE user_id = %s AND file_id = %s
               AND file_version_id = %s AND job_type = 'index_file' AND pipeline_version = %s""",
            (user_id, file_id, version_id, PIPELINE_VERSION),
        )).fetchone()
    if row is None:
        raise StorageNotFound("file job not found")
    return row


async def enqueue_user(user_id: UUID) -> dict:
    """A rebuild creates independently tracked jobs, scoped to one user."""
    from .file_index import _INDEXABLE_SUFFIXES

    if user_id is None:
        raise ValueError("user_id is required")
    async with db.db() as conn, conn.transaction():
        rows = await (await conn.execute(
            """SELECT f.id, f.current_version_id, f.logical_path FROM files f
               JOIN file_versions v ON v.user_id = f.user_id AND v.file_id = f.id
                 AND v.id = f.current_version_id
               WHERE f.user_id = %s AND f.kind = 'file' AND f.deleted_at IS NULL
                 AND v.upload_status = 'completed' ORDER BY f.id FOR UPDATE OF f""", (user_id,),
        )).fetchall()
        jobs = [await create(conn, user_id, r["id"], r["current_version_id"])
                for r in rows if r["logical_path"].lower().endswith(_INDEXABLE_SUFFIXES)]
    return {"job_ids": [j["id"] for j in jobs], "total_seen": len(rows),
            "queued": sum(j["status"] in ACTIVE for j in jobs)}


async def claim(job_id: UUID, worker_id: str) -> dict | None:
    async with db.db() as conn, conn.transaction():
        job = await (await conn.execute(
            """SELECT j.* FROM jobs j JOIN file_versions v
                 ON v.user_id = j.user_id AND v.file_id = j.file_id AND v.id = j.file_version_id
               WHERE j.id = %s AND j.status IN ('queued', 'retry_wait')
                 AND j.next_run_at <= clock_timestamp()
                 AND (j.job_type='storage_cleanup' OR v.upload_status = 'completed')
               FOR UPDATE OF j SKIP LOCKED""", (job_id,),
        )).fetchone()
        if job is None:
            return None
        execution_id = uuid4()
        job = await (await conn.execute(
            """UPDATE jobs SET status = 'running', attempt_count = attempt_count + 1,
                 execution_id = %s, lease_until = clock_timestamp() + %s * interval '1 second',
                 started_at = clock_timestamp(), finished_at = NULL, updated_at = clock_timestamp(),
                 error_code = NULL, last_error = NULL
               WHERE id = %s AND user_id = %s RETURNING *""",
            (execution_id, LEASE_SECONDS, job["id"], job["user_id"]),
        )).fetchone()
        await conn.execute(
            """INSERT INTO job_attempts(user_id, job_id, attempt_no, execution_id, worker_id)
               VALUES (%s, %s, %s, %s, %s)""",
            (job["user_id"], job["id"], job["attempt_count"], execution_id, worker_id),
        )
    _log("job.claimed", job, worker_id=worker_id)
    return job


async def lock_execution(conn, job) -> bool:
    """Lock before checking the database clock, including time spent waiting for locks."""
    row = await (await conn.execute(
        """SELECT lease_until FROM jobs WHERE user_id = %s AND id = %s
           AND status = 'running' AND execution_id = %s FOR UPDATE""",
        (job["user_id"], job["id"], job["execution_id"]),
    )).fetchone()
    if row is None:
        return False
    return (await (await conn.execute(
        "SELECT %s > clock_timestamp() AS valid", (row["lease_until"],),
    )).fetchone())["valid"]


async def finish(conn, job, status, code=None, message=None) -> None:
    """Caller holds a valid execution lock; index publication uses this same transaction."""
    updated = await (await conn.execute(
        """UPDATE jobs SET status = %s, error_code = %s, last_error = %s,
             lease_until = NULL, finished_at = clock_timestamp(), updated_at = clock_timestamp()
           WHERE user_id = %s AND id = %s AND execution_id = %s
             AND status = 'running' AND lease_until > clock_timestamp() RETURNING id""",
        (status, code, message, job["user_id"], job["id"], job["execution_id"]),
    )).fetchone()
    if updated is None:
        raise ExecutionExpired()
    await conn.execute(
        """UPDATE job_attempts SET status = %s, error_code = %s, error_message = %s,
             finished_at = clock_timestamp()
           WHERE user_id = %s AND job_id = %s AND execution_id = %s""",
        (status, code, message, job["user_id"], job["id"], job["execution_id"]),
    )
    _log("job." + status, job, error_code=code)


async def _retry_or_fail(conn, job, code, message, *, retryable, expired=False):
    failures = job['attempt_count']
    if job['job_type'] == 'storage_cleanup':
        failures = 1 + (await (await conn.execute(
            "SELECT count(*) AS n FROM job_attempts WHERE job_id=%s AND status IN ('failed','expired')",
            (job['id'],),
        )).fetchone())['n']
    retry = retryable and failures < MAX_ATTEMPTS
    delay = min(300, 5 * 2 ** min(failures - 1, 6))
    await conn.execute(
        """UPDATE job_attempts SET status = %s, error_code = %s, error_message = %s,
             finished_at = clock_timestamp()
           WHERE user_id = %s AND job_id = %s AND execution_id = %s""",
        ("expired" if expired else "failed", code, message,
         job["user_id"], job["id"], job["execution_id"]),
    )
    updated = await (await conn.execute(
        """UPDATE jobs SET status = %s, error_code = %s, last_error = %s,
             lease_until = NULL, execution_id = NULL, updated_at = clock_timestamp(),
             next_run_at = clock_timestamp() + %s * interval '1 second',
             finished_at = CASE WHEN %s THEN NULL ELSE clock_timestamp() END
           WHERE user_id = %s AND id = %s AND execution_id = %s AND status = 'running'
             AND (%s OR lease_until > clock_timestamp()) RETURNING id""",
        ("retry_wait" if retry else "failed", code, message, delay, retry,
         job["user_id"], job["id"], job["execution_id"], expired),
    )).fetchone()
    if updated is None:
        raise ExecutionExpired()
    if retry:
        await conn.execute(
            """INSERT INTO outbox(user_id, job_id, available_at)
               SELECT user_id, id, next_run_at FROM jobs WHERE user_id = %s AND id = %s""",
            (job["user_id"], job["id"]),
        )
    _log("job.retry_wait" if retry else "job.failed", job, error_code=code)


async def run(job_id: UUID, worker_id: str) -> None:
    from .file_index import IndexingError, PIPELINE_VERSION, index_file

    job = await claim(job_id, worker_id)
    if job is None:
        return
    token = request_id.set(job["request_id"])
    context = job_context.set({"job_id": job["id"], "user_id": job["user_id"],
                              "file_id": job["file_id"], "version_id": job["file_version_id"],
                              "attempt": job["attempt_count"], "execution_id": job["execution_id"],
                              "worker_id": worker_id})
    try:
        if job['job_type'] == 'storage_cleanup' and job['pipeline_version'] == '1':
            from . import storage_cleanup
            await storage_cleanup.run(job)
            return
        if job["job_type"] != "index_file" or job["pipeline_version"] != PIPELINE_VERSION:
            raise IndexingError("unsupported_pipeline", "This worker cannot process the requested pipeline")
        await index_file(job["user_id"], job["file_id"], job["file_version_id"], job=job)
    except Exception as exc:
        if isinstance(exc, IndexingError):
            code, message, retryable = exc.code, str(exc), exc.retryable
        elif isinstance(exc, StorageUnavailable):
            code, message, retryable = 'storage_unavailable', 'File storage is unavailable', True
        elif isinstance(exc, StorageConflict):
            code, message, retryable = 'cleanup_conflict', str(exc), False
        elif isinstance(exc, (OperationalError, PoolTimeout)):
            code, message, retryable = "database_unavailable", "Database is unavailable", True
        else:
            code, message, retryable = "processing_error", "File processing failed", False
            log.exception("job.error", extra={"event": "job.error", "job_id": job["id"]})
        # If the database is down this write may fail too. The lease recovery process
        # then records the expired attempt; it does not depend on this exception handler.
        async with db.db() as conn, conn.transaction():
            if await lock_execution(conn, job):
                await _retry_or_fail(conn, job, code, message, retryable=retryable)
    finally:
        job_context.reset(context)
        request_id.reset(token)


def _log(event, job, **extra):
    token = request_id.set(job.get("request_id"))
    try:
        log.info(event, extra={"event": event, "job_id": str(job["id"]),
                 "user_id": str(job["user_id"]), "file_id": str(job["file_id"]),
                 "version_id": str(job["file_version_id"]), "attempt": job["attempt_count"],
                 "execution_id": str(job["execution_id"]) if job["execution_id"] else None, **extra})
    finally:
        request_id.reset(token)


def public(job):
    return {key: job[key] for key in (
        "id", "file_id", "file_version_id", "status", "attempt_count", "next_run_at",
        "error_code", "last_error", "created_at", "started_at", "finished_at",
    )} | {"status_label": LABELS[job["status"]],
         "can_retry": job["status"] == "failed"
         and job["error_code"] in RETRYABLE | {"unsupported_pipeline"}
         and job["deleted_at"] is None and job["current_version_id"] == job["file_version_id"]}


async def get(user_id: UUID, job_id: UUID) -> dict:
    job = await db.fetchrow(
        """SELECT j.*, f.current_version_id, f.deleted_at FROM jobs j JOIN files f
             ON f.user_id = j.user_id AND f.id = j.file_id
           WHERE j.user_id = %s AND j.id = %s AND j.job_type='index_file'""", user_id, job_id,
    )
    if job is None:
        raise StorageNotFound("job not found")
    return public(job)


async def list_jobs(user_id: UUID, file_id: UUID | None = None) -> list[dict]:
    rows = await db.fetch(
        """SELECT j.*, f.current_version_id, f.deleted_at FROM jobs j JOIN files f
             ON f.user_id = j.user_id AND f.id = j.file_id
           WHERE j.user_id = %s AND j.job_type='index_file' AND (%s::uuid IS NULL OR j.file_id = %s)
           ORDER BY j.created_at DESC LIMIT 200""", user_id, file_id, file_id,
    )
    return [public(row) for row in rows]


async def retry(user_id: UUID, job_id: UUID) -> dict:
    async with db.db() as conn, conn.transaction():
        # Match the file -> job lock order used by completion and publication.
        file = await (await conn.execute(
            """SELECT f.* FROM files f JOIN jobs j ON j.user_id = f.user_id AND j.file_id = f.id
               WHERE j.user_id = %s AND j.id = %s AND j.job_type='index_file' FOR UPDATE OF f""", (user_id, job_id),
        )).fetchone()
        if file is None:
            raise StorageNotFound("job not found")
        job = await (await conn.execute(
            "SELECT * FROM jobs WHERE user_id = %s AND id = %s FOR UPDATE", (user_id, job_id),
        )).fetchone()
        if file["deleted_at"] is not None:
            raise StorageConflict("This file was deleted. Upload it again to process it.")
        if file["current_version_id"] != job["file_version_id"]:
            raise StorageConflict("A newer file version is current. Retry that version instead.")
        if job["status"] in ACTIVE:
            raise StorageConflict("This file is already waiting or processing.")
        if job["status"] != "failed":
            raise StorageConflict("Only failed processing can be retried.")
        if job["error_code"] not in RETRYABLE | {"unsupported_pipeline"}:
            raise StorageConflict("This processing error cannot be retried. Replace the file and upload it again.")
        await conn.execute(
            """UPDATE jobs SET status = 'queued', next_run_at = clock_timestamp(),
                 execution_id = NULL, lease_until = NULL, finished_at = NULL,
                 updated_at = clock_timestamp() WHERE user_id = %s AND id = %s""", (user_id, job_id),
        )
        await conn.execute("INSERT INTO outbox(user_id, job_id) VALUES (%s, %s)", (user_id, job_id))
    return await get(user_id, job_id)
