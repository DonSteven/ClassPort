"""B8 acceptance: interrupted copies, physical cleanup and version publication."""
import hashlib
from uuid import uuid4

import pytest

from app import db
from app.auth import SENTINEL_USER_ID as USER
from app.services import file_records as files, file_index, jobs, storage_cleanup, storage_migration
from app.storage import get_storage
from app.storage.base import ObjectRef, StorageConflict, StorageNotFound, StorageUnavailable
from app.storage.local import LocalStorage
from tests.services import test_s3 as s3_helpers
from tests.test_backup_recovery import recovery

s3 = s3_helpers.s3


@pytest.fixture(autouse=True)
def local_root(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDY_ROOT', str(tmp_path))
    monkeypatch.setenv('STORAGE_RETENTION_SECONDS', '0')
    monkeypatch.setenv('STORAGE_UPLOAD_GRACE_SECONDS', '1')


async def due_cleanup():
    return await db.fetch("SELECT * FROM jobs WHERE job_type='storage_cleanup' ORDER BY created_at,id")


async def expire_upload(version_id):
    await db.execute("UPDATE file_versions SET upload_expires_at=clock_timestamp()-interval '2 hours' WHERE id=%s", version_id)


async def test_migration_preserves_versions_search_and_sources(client, s3, monkeypatch):
    monkeypatch.setenv('STORAGE_BACKEND', 'local')
    old = await files.upload(USER, 'course/notes.txt', b'old text')
    current = await files.upload(USER, 'course/notes.txt', b'quantum mechanics')
    await file_index.index_file(USER, current['file_id'], current['version_id'])
    await files.move(USER, 'course', 'renamed')
    identities = await db.fetch('SELECT id,file_id,revision FROM file_versions ORDER BY revision')
    result = await storage_migration.migrate_user(USER)
    assert result['copied'] == 2 and result['remaining'] == 0 and result['errors'] == []
    assert await db.fetch('SELECT id,file_id,revision FROM file_versions ORDER BY revision') == identities
    assert (await files.current_version(USER, 'renamed/notes.txt'))['id'] == current['version_id']
    assert (await file_index.search(USER, 'quantum'))[0]['path'] == 'renamed/notes.txt'
    assert await files.read_version(USER, old['file_id'], old['version_id']) == b'old text'
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE job_type='index_file'") == 2
    for version in await db.fetch('SELECT * FROM storage_migrations'):
        assert await get_storage('local').read(ObjectRef(version['source_key']))
    assert (await storage_migration.migrate_user(USER))['reused'] == 2


async def test_migration_reuses_a_put_not_recorded_before_interruption(client, s3, monkeypatch):
    monkeypatch.setenv('STORAGE_BACKEND', 'local')
    target = await files.upload(USER, 'notes.txt', b'content')
    store = get_storage('s3')
    original = store.write

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise StorageUnavailable('interrupted after PUT')

    with monkeypatch.context() as patch:
        patch.setattr(store, 'write', interrupted)
        with pytest.raises(StorageUnavailable):
            await storage_migration.migrate_version(USER, target['version_id'])
    row = await db.fetchrow('SELECT * FROM file_versions WHERE id=%s', target['version_id'])
    assert row['storage_backend'] == 'local'
    assert len(await store.versions(row['object_key'])) == 1
    await storage_migration.migrate_version(USER, target['version_id'])
    assert len(await store.versions(row['object_key'])) == 1
    assert await db.fetchval('SELECT count(*) FROM file_versions') == 1


async def test_migration_reports_corrupt_source_without_switching_reference(client, s3, monkeypatch, tmp_path):
    monkeypatch.setenv('STORAGE_BACKEND', 'local')
    target = await files.upload(USER, 'notes.txt', b'correct')
    key = await db.fetchval('SELECT object_key FROM file_versions WHERE id=%s', target['version_id'])
    (tmp_path / key).write_bytes(b'corrupt')
    report = await storage_migration.migrate_user(USER)
    assert report['errors'] and report['remaining'] == 1
    assert await db.fetchval('SELECT storage_backend FROM file_versions WHERE id=%s', target['version_id']) == 'local'
    assert not await get_storage('s3').versions(key)


@pytest.mark.parametrize('after_delete', [False, True])
async def test_cleanup_retries_storage_failure_and_commit_interruption(client, monkeypatch, after_delete):
    target = await files.upload(USER, 'notes.txt', b'content')
    row = await files.get_version(USER, target['file_id'], target['version_id'])
    await files.delete(USER, ['notes.txt'])
    job = (await due_cleanup())[0]
    assert await files.list_files(USER) == []
    assert (await db.fetchrow('SELECT status FROM jobs WHERE id=%s', target['job_id']))['status'] == 'cancelled'

    async def fail(*args, **kwargs):
        raise StorageUnavailable('interrupted')

    with monkeypatch.context() as patch:
        patch.setattr(jobs if after_delete else LocalStorage, 'finish' if after_delete else 'delete', fail)
        await jobs.run(job['id'], 'cleanup-test')
    assert await db.fetchval('SELECT status FROM jobs WHERE id=%s', job['id']) == 'retry_wait'
    assert await db.fetchval('SELECT content_state FROM file_versions WHERE id=%s', row['id']) == 'retiring'
    await db.execute('UPDATE jobs SET next_run_at=clock_timestamp() WHERE id=%s', job['id'])
    await jobs.run(job['id'], 'cleanup-retry')
    assert await db.fetchval('SELECT status FROM jobs WHERE id=%s', job['id']) == 'succeeded'
    assert await db.fetchval('SELECT content_state FROM file_versions WHERE id=%s', row['id']) == 'purged'
    with pytest.raises(StorageNotFound):
        await get_storage('local').read(ObjectRef(row['object_key']))
    assert await db.fetchval('SELECT count(*) FROM files WHERE kind=\'file\'') == 1


async def test_cleanup_waits_for_a_recent_download_of_a_historical_version(client):
    old = await files.upload(USER, 'notes.txt', b'old')
    await files.upload(USER, 'notes.txt', b'new')
    row = await files.get_version(USER, old['file_id'], old['version_id'])
    await files.version_url(USER, row)
    job = (await due_cleanup())[0]
    await jobs.run(job['id'], 'cleanup-test')
    state = await db.fetchrow('SELECT status,next_run_at>clock_timestamp() AS future FROM jobs WHERE id=%s', job['id'])
    assert state == {'status': 'queued', 'future': True}
    assert await get_storage('local').read(ObjectRef(row['object_key'])) == b'old'


async def test_abandoned_upload_and_delete_markers_are_cleaned(client, s3):
    target, row, version = await s3_helpers.direct(s3)
    s3.delete_object(Bucket=get_storage('s3').bucket, Key=row['object_key'])
    await expire_upload(row['id'])
    await storage_cleanup.scan_user(USER)
    with pytest.raises(StorageConflict, match='abandoned'):
        await files.complete_upload(USER, target['file_id'], target['version_id'], version)
    for job in await due_cleanup():
        await jobs.run(job['id'], 'cleanup-test')
    assert await get_storage('s3').versions(row['object_key']) == []
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE job_type='index_file'") == 0


async def test_replayed_put_is_removed_while_confirmed_version_and_neighbor_survive(client, s3):
    target, row, version = await s3_helpers.direct(s3)
    await files.complete_upload(USER, target['file_id'], target['version_id'], version)
    s3_helpers.put(s3, row['object_key'], b'not confirmed')
    neighbor = s3_helpers.put(s3, row['object_key'] + '-neighbor', b'neighbor')
    await expire_upload(row['id'])
    await storage_cleanup.scan_user(USER)
    for job in await due_cleanup():
        await jobs.run(job['id'], 'cleanup-test')
    assert await files.download(USER, 'notes.txt') == b'quantum mechanics'
    assert await get_storage('s3').versions(row['object_key']) == [ObjectRef(row['object_key'], version)]
    assert await get_storage('s3').read(ObjectRef(row['object_key'] + '-neighbor', neighbor)) == b'neighbor'


async def test_confirmation_rechecks_after_cleanup_claimed_during_verification(client, s3, monkeypatch):
    target, row, version = await s3_helpers.direct(s3)
    await expire_upload(row['id'])
    store = get_storage('s3')
    original = store.stat

    async def verify_then_cleanup(ref):
        info = await original(ref)
        await storage_cleanup.scan_user(USER)
        return info

    monkeypatch.setattr(store, 'stat', verify_then_cleanup)
    with pytest.raises(StorageConflict, match='abandoned'):
        await files.complete_upload(USER, target['file_id'], target['version_id'], version)
    assert await db.fetchval('SELECT current_version_id FROM files WHERE id=%s', target['file_id']) is None


async def test_local_migration_source_cleanup_keeps_s3_content(client, s3, monkeypatch, tmp_path):
    monkeypatch.setenv('STORAGE_BACKEND', 'local')
    target = await files.upload(USER, 'notes.txt', b'content')
    key = await db.fetchval('SELECT object_key FROM file_versions WHERE id=%s', target['version_id'])
    await storage_migration.migrate_version(USER, target['version_id'])
    assert await files.download(USER, 'notes.txt') == b'content'
    await storage_cleanup.scan_user(USER, local_sources=True)
    for job in await due_cleanup():
        with monkeypatch.context() as patch:
            patch.setenv('STUDY_ROOT', str(tmp_path / 'not-mounted'))
            await jobs.run(job['id'], 'unmounted-worker')
        assert await db.fetchval('SELECT status FROM jobs WHERE id=%s', job['id']) == 'retry_wait'
        assert (tmp_path / key).exists()
        await db.execute('UPDATE jobs SET next_run_at=clock_timestamp() WHERE id=%s', job['id'])
        await jobs.run(job['id'], 'cleanup-test')
    with pytest.raises(StorageNotFound):
        await get_storage('local').read(ObjectRef(key))
    assert await files.download(USER, 'notes.txt') == b'content'


async def test_internal_import_uses_owner_confirmation_deduplication_and_pause(client, monkeypatch):
    monkeypatch.setenv('INTERNAL_API_SECRET', 'test-import-secret')
    headers = {'X-Internal-Secret': 'test-import-secret'}
    base = '/api/internal/materials'
    metadata = {'user_id': str(USER), 'path': 'notes.txt', 'size': 7,
                'sha256': hashlib.sha256(b'content').hexdigest()}
    assert (await client.post(base + '/upload-url', json=metadata)).status_code == 401
    target = (await client.post(base + '/upload-url', json=metadata, headers=headers)).json()
    version_base = f"{base}/{target['file_id']}/versions/{target['version_id']}"
    response = await client.put(version_base + '/content', params={'user_id': str(USER)}, content=b'content', headers=headers)
    assert response.status_code == 200
    other = uuid4()
    await db.execute('INSERT INTO users(id,email) VALUES (%s,%s)', other, f'{other}@test')
    assert (await client.post(version_base + '/complete', json={'user_id': str(other)}, headers=headers)).status_code == 404
    first = (await client.post(version_base + '/complete', json={'user_id': str(USER)}, headers=headers)).json()
    repeated = (await client.post(version_base + '/complete', json={'user_id': str(USER)}, headers=headers)).json()
    assert first['job_id'] == repeated['job_id']
    assert (await client.post(base + '/upload-url', json=metadata, headers=headers)).json()['reused']
    from app.services import material_sync
    await material_sync.set_paused(USER, True)
    assert not await material_sync.paused(other)
    metadata['scheduled'] = True
    assert (await client.post(base + '/upload-url', json=metadata, headers=headers)).status_code == 409


async def test_backup_inventory_after_cleanup_keeps_current_content_and_old_backup(client, s3, tmp_path, monkeypatch):
    old = await files.upload(USER, 'notes.txt', b'old')
    state = await db.fetchval(recovery.STATE_SQL)
    copies = {}

    def material_aws(operation, *args):
        assert operation == 'get-object'
        key = args[args.index('--key') + 1]
        version = args[args.index('--version-id') + 1]
        response = s3.get_object(Bucket=get_storage('s3').bucket, Key=key, VersionId=version)
        from pathlib import Path
        Path(args[-1]).write_bytes(response['Body'].read())

    def upload(path, bucket, key):
        copies[key] = path.read_bytes()
        return {'key': key, 'version_id': 'independent-backup-version'}

    monkeypatch.setattr(recovery, 'material_aws', material_aws)
    monkeypatch.setattr(recovery, 'upload', upload)
    records = recovery.backup_materials(tmp_path, {'S3_BUCKET': get_storage('s3').bucket, 'BACKUP_BUCKET': 'backups'}, state, 'snapshot/')
    current = await files.upload(USER, 'notes.txt', b'new')
    for job in await due_cleanup():
        await jobs.run(job['id'], 'cleanup-test')
    assert await db.fetchval('SELECT content_state FROM file_versions WHERE id=%s', old['version_id']) == 'purged'
    state = await db.fetchval(recovery.STATE_SQL)
    assert [row['id'] for row in state['s3_references']] == [current['version_id']]
    assert copies[records[0]['backup']['key']] == b'old'
    assert await files.download(USER, 'notes.txt') == b'new'


async def test_web_and_telegram_trigger_only_the_versioned_account_workflow(client, monkeypatch):
    import httpx
    from app.auth import require_user, _sentinel_user
    from app.services import material_sync, telegram
    client._transport.app.dependency_overrides[require_user] = lambda: _sentinel_user()
    monkeypatch.setenv('N8N_MATERIALS_WEBHOOK_URL', 'https://workflow.example.test/import')
    monkeypatch.delenv('N8N_MATERIALS_PROTOCOL', raising=False)
    assert (await client.post('/api/files/sync-materials')).status_code == 503
    monkeypatch.setenv('N8N_MATERIALS_PROTOCOL', 'versioned-v1')
    requests = []

    class Workflow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            requests.append(json)
            return httpx.Response(200, request=httpx.Request('POST', url), json={
                'protocol': 'versioned-v1', 'written_count': 1, 'errors': [],
            })

    monkeypatch.setattr(material_sync.httpx, 'AsyncClient', lambda **kwargs: Workflow())
    assert (await client.post('/api/files/sync-materials')).status_code == 200
    assert '1 files' in await telegram.handle_command('/sync', chat_id=1, user_id=USER)
    assert requests == [{'protocol': 'versioned-v1', 'user_id': str(USER), 'course': None}] * 2
    await telegram.handle_command('/pause', chat_id=1, user_id=USER)
    assert await material_sync.paused(USER)


async def test_restore_discards_source_cleanup_targets_without_losing_identity(client):
    target = await files.upload(USER, 'notes.txt', b'content')
    await files.delete(USER, ['notes.txt'])
    await db.execute("UPDATE file_versions SET content_state='retiring' WHERE id=%s", target['version_id'])
    assert await db.fetchval('SELECT count(*) FROM storage_cleanup') == 1
    async with db.db() as conn, conn.transaction():
        await conn.execute(recovery.RESET_STORAGE_SQL)
    assert await db.fetchval('SELECT count(*) FROM storage_cleanup') == 0
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE job_type='storage_cleanup'") == 0
    assert await db.fetchval('SELECT content_state FROM file_versions WHERE id=%s', target['version_id']) == 'purged'
    assert await db.fetchval('SELECT id FROM files WHERE id=%s', target['file_id']) == target['file_id']
    await storage_cleanup.scan_user(USER)
    assert await db.fetchval('SELECT count(*) FROM storage_cleanup') == 0
