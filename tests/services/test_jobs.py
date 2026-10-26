"""Durable task state, transaction boundaries, identity and recovery."""
from uuid import uuid4

import pytest

from app import db
from app.auth import SENTINEL_USER_ID as USER
from app.job_coordination import dispatch_once, recover_once
from app.services import file_index, file_records as files, jobs
from app.storage.base import StorageConflict, StorageUnavailable
from app.storage.local import LocalStorage


@pytest.fixture(autouse=True)
def object_root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))


async def test_completion_is_atomic_and_idempotent_without_a_broker(client, monkeypatch):
    target = await files.begin_upload(USER, "notes.txt")
    with pytest.raises(StorageConflict):
        await files.complete_upload(USER, target["file_id"], target["version_id"])
    await files.accept_upload(USER, target["file_id"], target["version_id"], b"quantum mechanics")
    assert await db.fetchval("SELECT current_version_id FROM files WHERE id=%s", target["file_id"]) is None
    original = jobs.create

    async def fail(*args):
        await original(*args)
        raise RuntimeError("interrupted before commit")

    monkeypatch.setattr(jobs, "create", fail)
    with pytest.raises(RuntimeError):
        await files.complete_upload(USER, target["file_id"], target["version_id"])
    assert await db.fetchval("SELECT upload_status FROM file_versions") == "pending"
    assert await db.fetchval("SELECT count(*) FROM jobs") == 0
    assert await db.fetchval("SELECT count(*) FROM outbox") == 0
    monkeypatch.setattr(jobs, "create", original)
    first = await files.complete_upload(USER, target["file_id"], target["version_id"])
    assert first == await files.complete_upload(USER, target["file_id"], target["version_id"])
    assert first["job_status"] == "queued"
    assert await db.fetchval("SELECT count(*) FROM jobs") == 1
    assert await db.fetchval("SELECT count(*) FROM outbox") == 1


async def test_duplicate_delivery_runs_one_attempt_and_publishes_search(client):
    upload = await files.upload(USER, "notes.txt", b"quantum mechanics")
    await jobs.run(upload["job_id"], "worker-1")
    await jobs.run(upload["job_id"], "worker-2")
    state = await jobs.get(USER, upload["job_id"])
    assert state["status"] == "succeeded" and state["attempt_count"] == 1
    assert await db.fetchval("SELECT status FROM job_attempts") == "succeeded"
    assert len(await file_index.search(USER, "quantum")) == 1
    assert (await files.list_files(USER))[0]["searchable"] is True


async def test_index_and_success_are_one_transaction(client, monkeypatch):
    upload = await files.upload(USER, "notes.txt", b"quantum mechanics")

    async def fail(*args):
        raise RuntimeError("interrupted before job success")

    monkeypatch.setattr(jobs, "finish", fail)
    await jobs.run(upload["job_id"], "worker")
    assert await db.fetchval("SELECT count(*) FROM file_index") == 0
    assert (await jobs.get(USER, upload["job_id"]))["status"] == "failed"


async def test_expired_execution_cannot_publish_after_recovery(client):
    upload = await files.upload(USER, "notes.txt", b"quantum mechanics")
    old = await jobs.claim(upload["job_id"], "old-worker")
    assert await jobs.claim(upload["job_id"], "duplicate-worker") is None
    await db.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second'")
    assert (await recover_once())["expired"] == 1
    assert (await file_index.index_file(USER, upload["file_id"], upload["version_id"], job=old))["reason"] == "execution_expired"
    assert await db.fetchval("SELECT count(*) FROM file_index") == 0
    await db.execute("UPDATE jobs SET next_run_at=clock_timestamp()")
    await jobs.run(upload["job_id"], "new-worker")
    assert (await jobs.get(USER, upload["job_id"]))["status"] == "succeeded"
    assert await db.fetchval("SELECT count(*) FROM job_attempts") == 2
    assert await db.fetchval("SELECT status FROM job_attempts WHERE attempt_no=1") == "expired"


async def test_expired_lease_alone_rejects_publication(client):
    upload = await files.upload(USER, "notes.txt", b"quantum mechanics")
    execution = await jobs.claim(upload["job_id"], "worker")
    await db.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second'")
    result = await file_index.index_file(USER, upload["file_id"], upload["version_id"], job=execution)
    assert result["reason"] == "execution_expired"
    assert await db.fetchval("SELECT count(*) FROM file_index") == 0
    assert await db.fetchval("SELECT status FROM jobs") == "running"


async def test_database_metrics_and_restore_recover_running_work(client):
    from app.job_coordination import metrics
    upload = await files.upload(USER, "notes.txt", b"quantum mechanics")
    await jobs.claim(upload["job_id"], "old-host")
    assert (await metrics())["JobExpiredLeases"] == 0
    assert (await recover_once(restored=True))["expired"] == 1
    assert (await jobs.get(USER, upload["job_id"]))["status"] == "retry_wait"
    assert await db.fetchval("SELECT status FROM job_attempts") == "expired"
    assert (await metrics())["JobRetries"] == 0


async def test_transient_errors_wait_then_retry_and_exhaust_budget(client, monkeypatch):
    upload = await files.upload(USER, "notes.txt", b"quantum mechanics")

    async def fail(*args):
        raise StorageUnavailable("private location must not be exposed")

    monkeypatch.setattr(LocalStorage, "read", fail)
    for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
        await jobs.run(upload["job_id"], "worker")
        state = await jobs.get(USER, upload["job_id"])
        assert state["attempt_count"] == attempt
        assert state["status"] == ("failed" if attempt == jobs.MAX_ATTEMPTS else "retry_wait")
        assert "private" not in state["last_error"]
        await jobs.run(upload["job_id"], "early-notification")
        assert (await jobs.get(USER, upload["job_id"]))["attempt_count"] == attempt
        await db.execute("UPDATE jobs SET next_run_at=clock_timestamp()")
    assert state["can_retry"] is True
    await jobs.retry(USER, upload["job_id"])
    assert (await jobs.get(USER, upload["job_id"]))["status"] == "queued"


async def test_corrupt_pdf_is_permanent_and_other_files_continue(client):
    broken = await files.upload(USER, "broken.pdf", b"invalid PDF")
    good = await files.upload(USER, "notes.txt", b"quantum mechanics")
    await jobs.run(broken["job_id"], "worker")
    await db.execute("UPDATE jobs SET next_run_at=clock_timestamp()-interval '1 minute' WHERE id=%s", good["job_id"])
    await jobs.run(good["job_id"], "worker")
    state = await jobs.get(USER, broken["job_id"])
    assert state["error_code"] == "invalid_pdf" and state["can_retry"] is False
    with pytest.raises(StorageConflict, match="Replace the file"):
        await jobs.retry(USER, broken["job_id"])
    assert (await jobs.get(USER, good["job_id"]))["status"] == "succeeded"


@pytest.mark.parametrize(("status", "error_code", "reason"), [
    ("queued", None, "already waiting or processing"),
    ("running", None, "already waiting or processing"),
    ("retry_wait", "worker_lost", "already waiting or processing"),
    ("succeeded", None, "Only failed processing"),
    ("cancelled", "version_changed", "Only failed processing"),
])
async def test_retry_explains_status_and_does_not_create_notifications(client, status, error_code, reason):
    upload = await files.upload(USER, "notes.txt", b"quantum mechanics")
    await db.execute("UPDATE jobs SET status=%s, error_code=%s", status, error_code)
    with pytest.raises(StorageConflict, match=reason):
        await jobs.retry(USER, upload["job_id"])
    assert await db.fetchval("SELECT count(*) FROM outbox") == 1
    assert (await jobs.get(USER, upload["job_id"]))["can_retry"] is False


async def test_dispatch_interruption_duplicates_and_lost_notifications_recover(client):
    upload = await files.upload(USER, "notes.txt", b"quantum mechanics")
    notifications = []

    def interrupted(job_id):
        notifications.append(job_id)
        raise RuntimeError("sent, but process terminated before marking")

    with pytest.raises(RuntimeError):
        await dispatch_once(interrupted)
    assert await db.fetchval("SELECT status FROM outbox") == "pending"
    await dispatch_once(notifications.append)
    assert notifications == [upload["job_id"], upload["job_id"]]
    await db.execute("UPDATE outbox SET created_at=clock_timestamp()-interval '2 minutes'")
    assert (await recover_once())["redispatched"] == 1
    assert (await recover_once())["redispatched"] == 0
    assert await db.fetchval("SELECT count(*) FROM outbox WHERE status='pending'") == 1


async def test_outdated_deleted_and_unsupported_pipeline_jobs_cannot_publish(client):
    old = await files.upload(USER, "notes.txt", b"old text")
    current = await files.upload(USER, "notes.txt", b"quantum mechanics")
    assert (await jobs.get(USER, old["job_id"]))["status"] == "cancelled"
    with pytest.raises(StorageConflict, match="newer file version"):
        await jobs.retry(USER, old["job_id"])
    await jobs.run(old["job_id"], "worker")
    await db.execute("UPDATE jobs SET pipeline_version='not-supported' WHERE id=%s", current["job_id"])
    await jobs.run(current["job_id"], "worker")
    assert (await jobs.get(USER, current["job_id"]))["error_code"] == "unsupported_pipeline"
    await files.delete(USER, ["notes.txt"])
    with pytest.raises(StorageConflict, match="file was deleted"):
        await jobs.retry(USER, current["job_id"])
    assert await db.fetchval("SELECT count(*) FROM file_index") == 0


async def test_api_completion_and_jobs_are_scoped_to_authenticated_user(client):
    from app.auth import _sentinel_user, require_user
    client._transport.app.dependency_overrides[require_user] = lambda: _sentinel_user()
    other = uuid4()
    await db.execute("INSERT INTO users(id,email) VALUES (%s,%s)", other, f"{other}@test")
    other_upload = await files.upload(other, "other.txt", b"private text")
    assert (await client.get(f"/api/jobs/{other_upload['job_id']}")).status_code == 404
    assert (await client.post(f"/api/jobs/{other_upload['job_id']}/retry")).status_code == 404
    assert (await client.get("/api/jobs")).json() == []
    other_complete = f"/api/files/{other_upload['file_id']}/versions/{other_upload['version_id']}/complete"
    assert (await client.post(other_complete)).status_code == 404
    target = (await client.post("/api/files/upload-url", json={"path": "notes.txt"})).json()
    assert (await client.put(target["url"], content=b"quantum mechanics")).status_code == 200
    complete = f"/api/files/{target['file_id']}/versions/{target['version_id']}/complete"
    response = await client.post(complete)
    first = response.json()
    assert await db.fetchval("SELECT request_id FROM jobs WHERE id=%s", first["job_id"]) == response.headers["x-request-id"]
    assert first == (await client.post(complete)).json()
    assert (await client.get(f"/api/jobs/{first['job_id']}")).json()["status"] == "queued"
    assert len((await client.get("/api/jobs", params={"file_id": target["file_id"]})).json()) == 1


async def test_metrics_exclude_retention_backoff_and_historical_retries(client):
    from app.job_coordination import metrics
    old = await files.upload(USER, 'old.txt', b'old retry history')
    await db.execute("UPDATE jobs SET created_at=clock_timestamp()-interval '7 days', next_run_at=clock_timestamp()-interval '10 seconds', attempt_count=3 WHERE id=%s", old['job_id'])
    for number in range(1, 4):
        await db.execute("""INSERT INTO job_attempts(user_id,job_id,attempt_no,execution_id,worker_id,status,started_at,finished_at)
            VALUES (%s,%s,%s,%s,'history','failed',clock_timestamp()-interval '2 hours',clock_timestamp()-interval '1 hour')""",
            USER, old['job_id'], number, uuid4())
    future = await files.upload(USER, 'future.txt', b'future backoff')
    await db.execute("UPDATE jobs SET next_run_at=clock_timestamp()+interval '1 day' WHERE id=%s", future['job_id'])
    measured = await metrics()
    assert 9 <= measured['JobWaitingAgeSeconds'] < 15
    assert measured['JobWaitingCount'] == 1
    assert measured['JobRetries'] == 0
    assert measured['JobProcessingSamples'] == 0
    await db.execute("""INSERT INTO job_attempts(user_id,job_id,attempt_no,execution_id,worker_id)
        VALUES (%s,%s,4,%s,'new-retry')""", USER, old['job_id'], uuid4())
    assert (await metrics())['JobRetries'] == 1


async def test_metrics_split_cleanup_and_exclude_deferred_or_expired_processing(client):
    from app.job_coordination import metrics
    from app.services import storage_cleanup
    from app.storage.base import ObjectRef
    uploaded = await files.upload(USER, 'notes.txt', b'quantum mechanics')
    await jobs.run(uploaded['job_id'], 'index-worker')
    await db.execute("""UPDATE job_attempts SET started_at=clock_timestamp()-interval '4 seconds',
        finished_at=clock_timestamp()-interval '2 seconds' WHERE job_id=%s""", uploaded['job_id'])
    version = await files.get_version(USER, uploaded['file_id'], uploaded['version_id'])
    async with db.db() as conn:
        now = (await (await conn.execute('SELECT clock_timestamp() AS now')).fetchone())['now']
        await storage_cleanup.enqueue(conn, version, ObjectRef(version['object_key']), 'content', now)
    cleanup_id = await db.fetchval("SELECT id FROM jobs WHERE job_type='storage_cleanup'")
    for number, status in enumerate(('cancelled', 'expired', 'succeeded'), 1):
        await db.execute("""INSERT INTO job_attempts(user_id,job_id,attempt_no,execution_id,worker_id,status,started_at,finished_at)
            VALUES (%s,%s,%s,%s,'cleanup',%s,clock_timestamp()-interval '110 seconds',clock_timestamp()-interval '10 seconds')""",
            USER, cleanup_id, number, uuid4(), status)
    measured = await metrics()
    assert measured['IndexJobProcessingSamples'] == 1
    assert measured['IndexJobProcessingP50Seconds'] == pytest.approx(2, abs=0.05)
    assert measured['IndexJobProcessingP95Seconds'] == pytest.approx(2, abs=0.05)
    assert measured['CleanupJobProcessingSamples'] == 1
    assert measured['CleanupJobProcessingSeconds'] == pytest.approx(100, abs=0.05)
    assert measured['CleanupJobExpiredAttempts'] == 1
    # Attempt 2 followed a normal deferral; only attempt 3 followed an interrupted execution.
    assert measured['CleanupJobRetries'] == 1 and measured['IndexJobRetries'] == 0
    assert measured['JobCompletedCount'] == 1
