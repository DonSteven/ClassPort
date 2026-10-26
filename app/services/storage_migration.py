"""Copy registered local versions without changing application identities."""
import hashlib
from datetime import timedelta

from .. import db
from ..storage import get_storage
from ..storage.base import ObjectRef, StorageConflict, StorageNotFound
from ..storage_config import retention_seconds
from .file_records import _lock_tree


async def migrate_version(user_id, version_id):
    store = get_storage('s3')
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        row = await (await conn.execute(
            "SELECT * FROM file_versions WHERE user_id=%s AND id=%s FOR UPDATE", (user_id, version_id),
        )).fetchone()
        if row is None:
            raise StorageNotFound('file version not found')
        if row['content_state'] != 'available' or row['upload_status'] != 'completed':
            raise StorageConflict('only available completed versions can be migrated')
        migration = await (await conn.execute(
            'SELECT * FROM storage_migrations WHERE file_version_id=%s', (version_id,),
        )).fetchone()
        if migration and migration['target_bucket'] != store.bucket:
            raise StorageConflict('migration belongs to another target bucket')
        if row['storage_backend'] == 's3':
            ref = ObjectRef(row['object_key'], row['object_version'])
        else:
            if not migration:
                migration = await (await conn.execute(
                    """INSERT INTO storage_migrations(user_id,file_id,file_version_id,source_key,
                           target_bucket,target_key,retain_until)
                       VALUES (%s,%s,%s,%s,%s,%s,clock_timestamp()+%s*interval '1 second') RETURNING *""",
                    (user_id, row['file_id'], version_id, row['object_key'], store.bucket,
                     row['object_key'], retention_seconds()),
                )).fetchone()
            ref = None
    if ref:
        info = await store.stat(ref)
        if (info.size, info.sha256) != (row['size'], row['sha256']):
            raise StorageConflict('migrated object differs from registered content')
        return {'status': 'reused', 'bytes': row['size']}
    data = await get_storage('local').read(ObjectRef(migration['source_key']))
    if (len(data), hashlib.sha256(data).hexdigest()) != (row['size'], row['sha256']):
        raise StorageConflict('local source differs from registered content')
    # Discover an upload whose VersionId was not committed before interruption.
    for candidate in await store.versions(migration['target_key'], include_delete_markers=False):
        try:
            info = await store.stat(candidate)
        except StorageNotFound:
            continue  # A version may have been removed since enumeration.
        if (info.size, info.sha256, info.content_type) == (row['size'], row['sha256'], row['content_type']):
            ref = candidate
            break
    if ref is None:
        ref = await store.write(ObjectRef(migration['target_key']), data, content_type=row['content_type'])
    info = await store.stat(ref)
    if (info.size, info.sha256, info.content_type) != (row['size'], row['sha256'], row['content_type']):
        raise StorageConflict('copied S3 object failed verification')
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        current = await (await conn.execute(
            'SELECT * FROM file_versions WHERE user_id=%s AND id=%s FOR UPDATE', (user_id, version_id),
        )).fetchone()
        if current['content_state'] != 'available':
            raise StorageConflict('source content was retired during migration')
        if current['storage_backend'] == 's3':
            return {'status': 'reused', 'bytes': row['size']}
        if current['object_key'] != migration['source_key']:
            raise StorageConflict('source mapping changed during migration')
        await conn.execute(
            """UPDATE file_versions SET storage_backend='s3',object_key=%s,object_version=%s
               WHERE user_id=%s AND id=%s""", (ref.key, ref.version, user_id, version_id),
        )
        now = (await (await conn.execute('SELECT clock_timestamp() AS now')).fetchone())['now']
        retain = max(now + timedelta(seconds=retention_seconds()), current['read_expires_at'])
        await conn.execute(
            """UPDATE storage_migrations SET target_version=%s,status='completed',completed_at=clock_timestamp(),
               retain_until=%s,last_error=NULL WHERE file_version_id=%s""", (ref.version, retain, version_id),
        )
        # A previously queued local-content deletion now concerns the migration source.
        await conn.execute(
            """UPDATE storage_cleanup SET reason='migration_source',not_before=greatest(not_before,%s)
               WHERE file_version_id=%s AND storage_backend='local' AND completed_at IS NULL""",
            (retain, version_id),
        )
        await conn.execute(
            """UPDATE jobs j SET status='cancelled',error_code='migration_source',finished_at=clock_timestamp()
               FROM storage_cleanup c WHERE j.cleanup_id=c.id AND c.file_version_id=%s
                 AND c.storage_backend='local' AND j.status IN ('queued','retry_wait')""", (version_id,),
        )
    return {'status': 'copied', 'bytes': row['size']}


async def migrate_user(user_id, *, limit=100):
    rows = await db.fetch(
        """SELECT id FROM file_versions WHERE user_id=%s AND upload_status='completed'
           AND content_state='available' ORDER BY (storage_backend='local') DESC,id LIMIT %s""", user_id, limit,
    )
    report = {'user_id': str(user_id), 'copied': 0, 'reused': 0, 'bytes': 0, 'versions': [], 'errors': []}
    for row in rows:
        try:
            result = await migrate_version(user_id, row['id'])
            report[result['status']] += 1
            report['bytes'] += result['bytes']
            report['versions'].append({'version_id': row['id'], **result})
        except (OSError, ValueError) as exc:
            # Safe diagnostics only; SDK exceptions are translated by storage.
            report['errors'].append({'version_id': row['id'], 'error': str(exc)})
            await db.execute('UPDATE storage_migrations SET last_error=%s WHERE file_version_id=%s',
                             str(exc), row['id'])
    report['remaining'] = await db.fetchval(
        """SELECT count(*) FROM file_versions WHERE user_id=%s AND storage_backend='local'
           AND upload_status='completed' AND content_state='available'""", user_id,
    )
    report.update(await db.fetchrow(
        """SELECT count(*) AS total_versions,count(DISTINCT file_id) AS files,
           coalesce(sum(size),0)::bigint AS total_bytes FROM file_versions
           WHERE user_id=%s AND upload_status='completed' AND content_state='available'""", user_id,
    ))
    return report
