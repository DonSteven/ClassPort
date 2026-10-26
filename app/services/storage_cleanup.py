"""Durable deletion of fixed physical objects; logical records remain intact."""
from datetime import timedelta
import os

from .. import db
from ..storage import get_storage
from ..storage.base import ObjectRef, StorageConflict, StorageNotFound, StorageUnavailable
from ..storage_config import retention_seconds, upload_grace_seconds


async def enqueue(conn, version, ref, reason, not_before, *, backend=None, bucket=None):
    backend = backend or version['storage_backend']
    bucket = (os.environ.get('S3_BUCKET', '') if backend == 's3' else '') if bucket is None else bucket
    if backend == 's3' and not ref.version:
        return
    target = await (await conn.execute(
        """INSERT INTO storage_cleanup(user_id,file_id,file_version_id,storage_backend,bucket,
               object_key,object_version,reason,not_before)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (storage_backend,bucket,object_key,object_version)
           DO UPDATE SET reason=EXCLUDED.reason,not_before=greatest(storage_cleanup.not_before,EXCLUDED.not_before)
           WHERE storage_cleanup.completed_at IS NULL RETURNING id,not_before""",
        (version['user_id'], version['file_id'], version['id'], backend, bucket,
         ref.key, ref.version or '', reason, not_before),
    )).fetchone()
    if target is None:
        return
    not_before = target['not_before']
    job = await (await conn.execute(
        """INSERT INTO jobs(user_id,file_id,file_version_id,job_type,pipeline_version,cleanup_id,next_run_at)
           VALUES (%s,%s,%s,'storage_cleanup','1',%s,%s)
           ON CONFLICT (cleanup_id) WHERE cleanup_id IS NOT NULL DO NOTHING RETURNING id""",
        (version['user_id'], version['file_id'], version['id'], target['id'], not_before),
    )).fetchone()
    if job is None:
        job = await (await conn.execute(
            """UPDATE jobs SET status='queued',next_run_at=%s,execution_id=NULL,lease_until=NULL,
               finished_at=NULL,error_code=NULL,last_error=NULL WHERE cleanup_id=%s AND status='cancelled'
               RETURNING id""", (not_before, target['id']),
        )).fetchone()
    if job is None:
        return
    await conn.execute(
        'INSERT INTO outbox(user_id,job_id,available_at) VALUES (%s,%s,%s)',
        (version['user_id'], job['id'], not_before),
    )


def content_deadline(row):
    if row['upload_status'] in ('pending', 'abandoned'):
        return row['upload_expires_at'] + timedelta(seconds=upload_grace_seconds())
    since = row['deleted_at'] or row['retired_at']
    return max(since + timedelta(seconds=retention_seconds()), row['read_expires_at']) if since else None


async def schedule_content(conn, row):
    if row['content_state'] == 'purged':
        return
    deadline = content_deadline(row)
    if deadline:
        await enqueue(conn, row, ObjectRef(row['object_key'], row['object_version']), 'content', deadline)


async def scan_user(user_id, *, limit=100, local_sources=False):
    """Bounded reconciliation discovers abandoned uploads and replayed PUT versions."""
    from .file_records import _lock_tree

    rows = await db.fetch(
        """SELECT v.* FROM file_versions v WHERE v.user_id=%s
           ORDER BY cleanup_checked_at,id LIMIT %s""", user_id, limit,
    )
    for source in rows:
        # Listing is outside the database checkout. Eligibility is checked again below.
        refs = []
        if source['storage_backend'] == 's3':
            refs = await get_storage('s3').versions(source['object_key'])
        async with db.db() as conn, conn.transaction():
            await _lock_tree(conn, user_id)
            row = await (await conn.execute(
                """SELECT v.*,f.deleted_at FROM file_versions v JOIN files f
                   ON f.user_id=v.user_id AND f.id=v.file_id
                   WHERE v.user_id=%s AND v.id=%s FOR UPDATE OF f,v""", (user_id, source['id']),
            )).fetchone()
            now = (await (await conn.execute('SELECT clock_timestamp() AS now')).fetchone())['now']
            if (row['storage_backend'], row['object_key'], row['object_version']) != (
                source['storage_backend'], source['object_key'], source['object_version'],
            ):
                continue
            await schedule_content(conn, row)
            if now >= row['upload_expires_at'] + timedelta(seconds=upload_grace_seconds()):
                for ref in refs:
                    if ref.version != row['object_version'] or row['content_state'] == 'purged':
                        reason = 'content' if row['upload_status'] != 'completed' or row['content_state'] == 'purged' else 'orphan'
                        await enqueue(conn, row, ref, reason, now)
                if row['upload_status'] == 'pending':
                    await conn.execute("UPDATE file_versions SET upload_status='abandoned' WHERE id=%s", (row['id'],))
            migration = await (await conn.execute(
                "SELECT * FROM storage_migrations WHERE file_version_id=%s AND status='completed'",
                (row['id'],),
            )).fetchone()
            if migration and local_sources:
                await enqueue(conn, row, ObjectRef(migration['source_key']), 'migration_source',
                              migration['retain_until'], backend='local')
            await conn.execute('UPDATE file_versions SET cleanup_checked_at=clock_timestamp() WHERE id=%s', (row['id'],))
    return len(rows)


async def run(job):
    from . import jobs
    from .file_records import _lock_tree

    # Make the retirement durable before deleting bytes. Confirmation uses the
    # same user/file/version lock order and cannot publish retired content.
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, job['user_id'])
        row = await (await conn.execute(
            """SELECT v.*,f.deleted_at FROM file_versions v JOIN files f
               ON f.user_id=v.user_id AND f.id=v.file_id
               WHERE v.user_id=%s AND v.id=%s FOR UPDATE OF f,v""",
            (job['user_id'], job['file_version_id']),
        )).fetchone()
        if not await jobs.lock_execution(conn, job):
            return
        target = await (await conn.execute(
            'SELECT * FROM storage_cleanup WHERE user_id=%s AND id=%s FOR UPDATE',
            (job['user_id'], job['cleanup_id']),
        )).fetchone()
        now = (await (await conn.execute('SELECT clock_timestamp() AS now')).fetchone())['now']
        if target['completed_at']:
            await jobs.finish(conn, job, 'succeeded')
            return
        if target['not_before'] > now:
            await defer(conn, job, target['not_before'])
            return
        migration = await (await conn.execute(
            'SELECT * FROM storage_migrations WHERE file_version_id=%s', (row['id'],),
        )).fetchone()
        if migration and migration['status'] == 'copying':
            raise StorageUnavailable('storage migration must finish before cleanup')
        busy = await (await conn.execute(
            """SELECT 1 FROM jobs WHERE user_id=%s AND file_version_id=%s AND job_type='index_file'
               AND status='running' AND lease_until>clock_timestamp() LIMIT 1""",
            (job['user_id'], row['id']),
        )).fetchone()
        if busy:
            raise StorageUnavailable('a reader is still processing this version')
        ref = ObjectRef(target['object_key'], target['object_version'] or None)
        same = (row['storage_backend'], row['object_key'], row['object_version'] or '') == (
            target['storage_backend'], ref.key, ref.version or '',
        )
        if target['reason'] == 'content':
            deadline = content_deadline(row)
            # Unconfirmed S3 uploads have no bound VersionId; all their versions are garbage.
            pending = row['upload_status'] in ('pending', 'abandoned')
            if deadline and deadline > now:
                await defer(conn, job, deadline)
                return
            if not deadline or (not same and not pending):
                await jobs.finish(conn, job, 'cancelled', 'content_referenced', 'Content is still referenced')
                return
            await conn.execute(
                """UPDATE file_versions SET content_state='retiring',
                   upload_status=CASE WHEN upload_status='pending' THEN 'abandoned' ELSE upload_status END
                   WHERE id=%s""", (row['id'],),
            )
        elif target['reason'] == 'orphan':
            if same or now < row['upload_expires_at'] + timedelta(seconds=upload_grace_seconds()):
                await jobs.finish(conn, job, 'cancelled', 'content_referenced', 'Content is still referenced')
                return
        else:
            if not migration or migration['status'] != 'completed' or migration['source_key'] != ref.key or same:
                raise StorageConflict('migration source is still needed')
            if migration['retain_until'] > now:
                await defer(conn, job, migration['retain_until'])
                return
        if target['storage_backend'] == 's3' and target['bucket'] != os.environ.get('S3_BUCKET', ''):
            raise StorageConflict('cleanup target belongs to another bucket')
    try:
        store = get_storage(target['storage_backend'])
        if target['storage_backend'] == 'local' and not store.root.is_dir():
            raise StorageUnavailable('local material root is not mounted')
        await store.delete(ref)
    except StorageNotFound:
        pass  # A previous execution may have deleted the object before its commit.
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, job['user_id'])
        await conn.execute('SELECT id FROM file_versions WHERE id=%s FOR UPDATE', (row['id'],))
        if not await jobs.lock_execution(conn, job):
            return
        await conn.execute('UPDATE storage_cleanup SET completed_at=clock_timestamp() WHERE id=%s', (target['id'],))
        if target['reason'] == 'content':
            await conn.execute("UPDATE file_versions SET content_state='purged' WHERE id=%s", (row['id'],))
        await jobs.finish(conn, job, 'succeeded')


async def defer(conn, job, until):
    from . import jobs
    await jobs.finish(conn, job, 'cancelled', 'retained', 'Content retention was extended')
    await conn.execute(
        """UPDATE jobs SET status='queued',next_run_at=%s,finished_at=NULL,execution_id=NULL,
           error_code=NULL,last_error=NULL WHERE id=%s""", (until, job['id']),
    )
    await conn.execute('INSERT INTO outbox(user_id,job_id,available_at) VALUES (%s,%s,%s)',
                       (job['user_id'], job['id'], until))
