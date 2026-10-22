"""Index completed immutable versions and search through current file metadata."""
import json
from uuid import uuid4

import pytest

from app import db
from app.auth import SENTINEL_USER_ID as USER
from app.services import file_index as index
from app.services import file_records as files
from app.services import jobs
from app.storage import get_storage
from app.storage.base import ObjectRef


async def process_user(user_id):
    report = await jobs.enqueue_user(user_id)
    for job_id in report["job_ids"]:
        await jobs.run(job_id, "test-worker")
    return report


@pytest.fixture(autouse=True)
def object_root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))


def make_pdf(text):
    import fitz
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), text)
        return doc.tobytes()


@pytest.mark.parametrize("path,data,word", [
    ("lecture.pdf", None, "quantum"),
    ("notes.md", b"# quantum mechanics", "quantum"),
    ("notes.txt", b"quantum mechanics", "quantum"),
    ("notes.typ", b"quantum mechanics", "quantum"),
    ("notes.ipynb", json.dumps({"cells": [{"source": ["quantum", " mechanics"]}]}).encode(), "quantum"),
])
async def test_index_and_search_formats(client, path, data, word):
    version = await files.upload(USER, path, data if data is not None else make_pdf("quantum mechanics"))
    stats = await process_user(USER)
    assert stats["queued"] == 1
    assert (await jobs.get(USER, version["job_id"]))["status"] == "succeeded"
    result = await index.search(USER, word)
    assert result[0]["path"] == path and result[0]["file_id"] == version["file_id"]
    assert result[0]["version_id"] == version["version_id"]
    assert "<<" in result[0]["snippet"]
    assert (await process_user(USER))["queued"] == 0


async def test_search_tracks_rename_and_hides_replaced_or_deleted_version(client):
    await files.upload(USER, "course/lecture.txt", b"quantum mechanics")
    await process_user(USER)
    await files.move(USER, "course", "renamed")
    assert (await index.search(USER, "quantum"))[0]["path"] == "renamed/lecture.txt"
    await files.upload(USER, "renamed/lecture.txt", b"relativity principles")
    assert await index.search(USER, "quantum") == []
    await process_user(USER)
    assert (await index.search(USER, "relativity"))[0]["path"] == "renamed/lecture.txt"
    await files.delete(USER, ["renamed"])
    assert await index.search(USER, "relativity") == []
    assert (await process_user(USER))["total_seen"] == 0


async def test_failed_pdf_and_missing_object_do_not_clear_other_indexes(client):
    await files.upload(USER, "valid.txt", b"quantum mechanics")
    await process_user(USER)
    await files.upload(USER, "broken.pdf", b"not a pdf")
    version = await files.upload(USER, "missing.txt", b"missing content")
    row = await files.get_version(USER, version["file_id"], version["version_id"])
    await get_storage().delete(ObjectRef(row["object_key"]))
    await process_user(USER)
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE status = 'failed'") == 2
    assert len(await index.search(USER, "quantum")) == 1


async def test_index_and_search_are_user_scoped(client):
    other = uuid4()
    await db.execute("INSERT INTO users(id,email) VALUES (%s,%s)", other, f"{other}@test")
    await files.upload(USER, "same.txt", b"quantum physics")
    await files.upload(other, "same.txt", b"quantum chemistry")
    assert (await process_user(USER))["queued"] == 1
    assert await index.search(other, "quantum") == []
    assert (await process_user(other))["queued"] == 1
    assert len(await index.search(other, "quantum")) == 1
    assert "chemistry" not in str(await index.search(USER, "quantum"))


async def test_empty_short_query_unsupported_and_nuls(client):
    assert (await process_user(USER))["total_seen"] == 0
    await files.upload(USER, "image.png", b"not indexed")
    await files.upload(USER, "notes.txt", b"quantum\x00 mechanics")
    stats = await process_user(USER)
    assert stats["queued"] == 1 and stats["total_seen"] == 2
    assert await index.search(USER, "q") == []
    assert await index.search(USER, "nonsenseword") == []
    assert len(await index.search(USER, "quantum", limit=1)) == 1


async def test_single_file_reads_only_its_version_and_skips_duplicate(client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.storage.local import LocalStorage

    target = await files.upload(USER, "lecture.pdf", make_pdf("quantum mechanics"))
    await files.upload(USER, "other.txt", b"unrelated lecture")
    reader = AsyncMock(wraps=LocalStorage.read)

    async def read(store, ref):
        return await reader(store, ref)

    monkeypatch.setattr(LocalStorage, "read", read)
    result = await index.index_file(USER, target["file_id"], target["version_id"])
    assert result["status"] == "indexed"
    assert (await index.index_file(USER, target["file_id"], target["version_id"]))["status"] == "skipped"
    assert reader.await_count == 1
    assert (await db.fetchval("SELECT count(*) FROM file_index")) == 1
    assert await index.search(USER, "unrelated") == []


@pytest.mark.parametrize("path,data,code", [
    ("broken.pdf", b"not a pdf", "invalid_pdf"),
    ("broken.ipynb", b"{not json", "invalid_notebook"),
    ("image.png", b"image", "unsupported_format"),
])
async def test_single_file_permanent_errors_are_explicit(client, path, data, code):
    target = await files.upload(USER, path, data)
    with pytest.raises(index.IndexingError) as caught:
        await index.index_file(USER, target["file_id"], target["version_id"])
    assert caught.value.code == code and caught.value.retryable is False
    assert await db.fetchval("SELECT count(*) FROM file_index") == 0
    good = await files.upload(USER, "good.txt", b"quantum mechanics")
    assert (await index.index_file(USER, good["file_id"], good["version_id"]))["status"] == "indexed"


async def test_single_file_rejects_incomplete_upload_and_wrong_identity(client):
    pending = await files.begin_upload(USER, "pending.pdf")
    with pytest.raises(index.IndexingError) as caught:
        await index.index_file(USER, pending["file_id"], pending["version_id"])
    assert caught.value.code == "upload_incomplete"
    target = await files.upload(USER, "lecture.txt", b"quantum mechanics")
    for user_id, file_id, version_id in [
        (uuid4(), target["file_id"], target["version_id"]),
        (USER, pending["file_id"], target["version_id"]),
        (USER, target["file_id"], uuid4()),
    ]:
        with pytest.raises(index.IndexingError) as caught:
            await index.index_file(user_id, file_id, version_id)
        assert caught.value.code == "version_not_found"
    assert await db.fetchval("SELECT count(*) FROM file_index") == 0


async def test_same_content_revision_advances_index_reference(client):
    first = await files.upload(USER, "lecture.txt", b"quantum mechanics")
    await index.index_file(USER, first["file_id"], first["version_id"])
    second = await files.upload(USER, "lecture.txt", b"quantum mechanics")
    assert await index.search(USER, "quantum") == []
    await index.index_file(USER, second["file_id"], second["version_id"])
    assert (await index.search(USER, "quantum"))[0]["version_id"] == second["version_id"]
    assert (await index.index_file(USER, first["file_id"], first["version_id"]))["status"] == "discarded"


async def test_pipeline_change_reprocesses_unchanged_content(client, monkeypatch):
    target = await files.upload(USER, "lecture.txt", b"quantum mechanics")
    await index.index_file(USER, target["file_id"], target["version_id"])
    monkeypatch.setattr(index, "PIPELINE_VERSION", "2")
    assert (await index.index_file(USER, target["file_id"], target["version_id"]))["status"] == "indexed"
    assert await db.fetchval("SELECT pipeline_version FROM file_index") == "2"
    assert (await index.index_file(USER, target["file_id"], target["version_id"]))["status"] == "skipped"


async def test_failed_revision_keeps_old_index_hidden(client):
    original = await files.upload(USER, "lecture.pdf", make_pdf("quantum mechanics"))
    await index.index_file(USER, original["file_id"], original["version_id"])
    broken = await files.upload(USER, "lecture.pdf", b"not a pdf")
    await process_user(USER)
    failure = await jobs.get(USER, broken["job_id"])
    assert failure["status"] == "failed" and failure["error_code"] == "invalid_pdf"
    assert failure["can_retry"] is False
    assert await db.fetchval("SELECT file_version_id FROM file_index") == original["version_id"]
    assert await index.search(USER, "quantum") == []
    await files.delete(USER, ["lecture.pdf"])
    replacement = await files.upload(USER, "lecture.pdf", make_pdf("relativity principles"))
    assert replacement["file_id"] != broken["file_id"]
    await index.index_file(USER, replacement["file_id"], replacement["version_id"])
    assert (await index.search(USER, "relativity"))[0]["file_id"] == replacement["file_id"]


@pytest.mark.parametrize("failure,code,retryable", [
    ("missing", "content_missing", False),
    ("storage", "storage_unavailable", True),
    ("database", "database_unavailable", True),
])
async def test_single_file_infrastructure_error_classification(client, monkeypatch, failure, code, retryable):
    from psycopg import OperationalError
    from app.storage.base import StorageNotFound, StorageUnavailable
    from app.storage.local import LocalStorage

    target = await files.upload(USER, "lecture.txt", b"quantum mechanics")

    async def fail(*args):
        raise {"missing": StorageNotFound, "storage": StorageUnavailable,
               "database": OperationalError}[failure]("private internal location")

    if failure == "database":
        monkeypatch.setattr(db, "fetchrow", fail)
    else:
        monkeypatch.setattr(LocalStorage, "read", fail)
    with pytest.raises(index.IndexingError) as caught:
        await index.index_file(USER, target["file_id"], target["version_id"])
    assert caught.value.code == code and caught.value.retryable is retryable
    assert "private" not in str(caught.value)


async def test_valid_empty_pdf_is_distinct_from_failed_extraction(client):
    import fitz
    with fitz.open() as doc:
        doc.new_page()
        content = doc.tobytes()
    target = await files.upload(USER, "empty.pdf", content)
    assert (await index.index_file(USER, target["file_id"], target["version_id"]))["status"] == "indexed"
    assert await db.fetchval("SELECT text_content FROM file_index") == ""
