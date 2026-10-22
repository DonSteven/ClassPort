"""B3 publication races with real commits, row locks and a one-connection pool."""
import asyncio
import threading
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
import pytest
import pytest_asyncio

from app import db
from app.services import file_index as index
from app.services import file_records as files
from app.services import jobs
from app.storage.local import LocalStorage
from tests.services.test_file_index import make_pdf


@pytest_asyncio.fixture
async def committed_user(pg_url, monkeypatch):
    async with AsyncConnectionPool(
        pg_url, min_size=1, max_size=1, open=False,
        kwargs={"row_factory": dict_row}, configure=db._configure_connection,
    ) as pool:
        monkeypatch.setattr(db, "_pool", pool)
        user_id = uuid4()
        await db.execute("INSERT INTO users(id,email) VALUES (%s,%s)", user_id, f"{user_id}@test")
        try:
            yield user_id
        finally:
            await db.execute("DELETE FROM users WHERE id = %s", user_id)


@pytest.fixture
async def paused_pdf(committed_user, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))
    content = make_pdf("quantum mechanics")
    target = await files.upload(committed_user, "lecture.pdf", content)
    entered, release = threading.Event(), threading.Event()
    extract = index._extract_text

    def pause(path, data):
        if data == content:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test did not release PDF extraction")
        return extract(path, data)

    monkeypatch.setattr(index, "_extract_text", pause)
    task = asyncio.create_task(index.index_file(committed_user, target["file_id"], target["version_id"]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        yield target, release, task
    finally:
        release.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.parametrize("mutation", ["replace", "delete", "rename"])
async def test_late_pdf_cannot_publish_invalid_version(committed_user, paused_pdf, mutation):
    target, release, task = paused_pdf
    # A one-connection pool makes these calls fail if parsing retains a checkout.
    if mutation == "replace":
        newer = await asyncio.wait_for(files.upload(
            committed_user, "lecture.pdf", make_pdf("relativity principles")), 3)
        await index.index_file(committed_user, newer["file_id"], newer["version_id"])
    elif mutation == "delete":
        await asyncio.wait_for(files.delete(committed_user, ["lecture.pdf"]), 3)
    else:
        await asyncio.wait_for(files.move(committed_user, "lecture.pdf", "renamed.pdf"), 3)
    release.set()
    outcome = await asyncio.wait_for(task, 5)
    if mutation == "replace":
        assert outcome["status"] == "discarded" and outcome["reason"] == "version_changed"
        rows = await db.fetch("SELECT file_version_id FROM file_index WHERE user_id = %s", committed_user)
        assert rows == [{"file_version_id": newer["version_id"]}]
        assert await index.search(committed_user, "quantum") == []
        assert len(await index.search(committed_user, "relativity")) == 1
    elif mutation == "delete":
        assert outcome["status"] == "discarded" and outcome["reason"] == "file_deleted"
        assert await db.fetchval("SELECT count(*) FROM file_index WHERE user_id = %s", committed_user) == 0
        assert await index.search(committed_user, "quantum") == []
    else:
        assert outcome["status"] == "indexed"
        assert (await index.search(committed_user, "quantum"))[0]["path"] == "renamed.pdf"
        assert await db.fetchval("SELECT file_id FROM file_index WHERE user_id = %s", committed_user) == target["file_id"]


async def test_submission_waits_for_file_lock_then_rechecks(committed_user, paused_pdf, pg_url):
    target, release, task = paused_pdf
    async with await psycopg.AsyncConnection.connect(pg_url) as deleting:
        await deleting.execute("UPDATE files SET deleted_at = now() WHERE user_id = %s AND id = %s",
                               (committed_user, target["file_id"]))
        release.set()
        # Observe the actual SQL lock wait before allowing the delete to commit.
        async with await psycopg.AsyncConnection.connect(pg_url, autocommit=True) as observer:
            async with asyncio.timeout(5):
                while True:
                    blocked = await (await observer.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE %s = ANY(pg_blocking_pids(pid)))",
                        (deleting.info.backend_pid,),
                    )).fetchone()
                    if blocked[0]:
                        break
                    assert not task.done(), "index submission did not hold a file row lock"
                    await asyncio.sleep(0.02)
        await deleting.commit()
    assert (await asyncio.wait_for(task, 5))["status"] == "discarded"
    assert await db.fetchval("SELECT count(*) FROM file_index WHERE user_id = %s", committed_user) == 0


async def test_lease_expiring_during_index_write_rolls_back_the_entire_result(committed_user, pg_url, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))
    monkeypatch.setattr(jobs, "LEASE_SECONDS", 5)
    first = await files.upload(committed_user, "notes.txt", b"old text")
    await index.index_file(committed_user, first["file_id"], first["version_id"])
    newer = await files.upload(committed_user, "notes.txt", b"quantum mechanics")
    async with await psycopg.AsyncConnection.connect(pg_url) as locked:
        await locked.execute("SELECT file_id FROM file_index WHERE file_id=%s FOR UPDATE", (first["file_id"],))
        task = asyncio.create_task(jobs.run(newer["job_id"], "worker"))
        try:
            async with await psycopg.AsyncConnection.connect(pg_url, autocommit=True) as observer:
                async with asyncio.timeout(5):
                    while True:
                        blocked = await (await observer.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE %s = ANY(pg_blocking_pids(pid)))",
                            (locked.info.backend_pid,),
                        )).fetchone()
                        if blocked[0]:
                            break
                        assert not task.done()
                        await asyncio.sleep(0.05)
            await asyncio.sleep(5.1)
        finally:
            await locked.commit()
            await asyncio.wait_for(task, 5)
    assert await db.fetchval("SELECT file_version_id FROM file_index WHERE file_id=%s", first["file_id"]) == first["version_id"]
    assert (await jobs.get(committed_user, newer["job_id"]))["status"] == "running"
    assert await index.search(committed_user, "quantum") == []


async def test_storage_read_does_not_hold_connection(committed_user, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))
    target = await files.upload(committed_user, "lecture.txt", b"quantum mechanics")
    read = LocalStorage.read

    async def check_read(store, ref):
        assert await asyncio.wait_for(db.fetchval("SELECT 1"), 2) == 1
        return await read(store, ref)

    monkeypatch.setattr(LocalStorage, "read", check_read)
    assert (await index.index_file(committed_user, target["file_id"], target["version_id"]))["status"] == "indexed"
