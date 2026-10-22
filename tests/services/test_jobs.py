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
    await jobs.run(good["job_id"], "worker")
    state = await jobs.get(USER, broken["job_id"])
    assert state["error_code"] == "invalid_pdf" and state["can_retry"] is False
    with pytest.raises(StorageConflict):
        await jobs.retry(USER, broken["job_id"])
    assert (await jobs.get(USER, good["job_id"]))["status"] == "succeeded"


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
    await jobs.run(old["job_id"], "worker")
    await db.execute("UPDATE jobs SET pipeline_version='not-supported' WHERE id=%s", current["job_id"])
    await jobs.run(current["job_id"], "worker")
    assert (await jobs.get(USER, current["job_id"]))["error_code"] == "unsupported_pipeline"
    await files.delete(USER, ["notes.txt"])
    with pytest.raises(StorageConflict):
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
