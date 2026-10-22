"""B2 acceptance using a real database, temporary objects and authenticated routes."""
import hashlib
from uuid import uuid4

import pytest

from app import db
from app.auth import SENTINEL_USER_ID as USER
from app.services import file_records as files
from app.storage import get_storage
from app.storage.base import ObjectRef, StorageConflict, StorageNotFound
from scripts.import_local_files import import_user


@pytest.fixture(autouse=True)
def object_root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))


async def test_revision_keeps_identity_bytes_and_name_independent(client):
    v1 = await files.upload(USER, "COURSE/lecture.pdf", b"first")
    v2 = await files.upload(USER, "COURSE/lecture.pdf", b"second")
    assert v1["file_id"] == v2["file_id"] and v1["version_id"] != v2["version_id"]
    assert await files.read_version(USER, v1["file_id"], v1["version_id"]) == b"first"
    before = await files.get_version(USER, v2["file_id"], v2["version_id"])
    await files.move(USER, "COURSE", "RENAMED")
    after = await files.current_version(USER, "RENAMED/lecture.pdf")
    assert before["object_key"] == after["object_key"]
    assert after["file_id"] == v1["file_id"] and after["id"] == v2["version_id"]
    assert await files.download(USER, "RENAMED/lecture.pdf") == b"second"
    assert await files.list_files(USER, "COURSE") == []


async def test_frozen_put_and_late_upload(client):
    first = await files.begin_upload(USER, "lecture.pdf")
    newer = await files.upload(USER, "lecture.pdf", b"newer")
    await files.accept_upload(USER, first["file_id"], first["version_id"], b"older")
    assert (await files.current_version(USER, "lecture.pdf"))["id"] == newer["version_id"]
    with pytest.raises(StorageConflict):
        await files.accept_upload(USER, first["file_id"], first["version_id"], b"replacement")
    assert await files.read_version(USER, first["file_id"], first["version_id"]) == b"older"


async def test_interrupted_put_reuses_only_identical_bytes(client):
    target = await files.begin_upload(USER, "lecture.pdf")
    version = await db.fetchrow("SELECT * FROM file_versions WHERE id = %s", target["version_id"])
    await get_storage().write(ObjectRef(version["object_key"]), b"published")
    with pytest.raises(StorageConflict):
        await files.accept_upload(USER, target["file_id"], target["version_id"], b"different")
    await files.accept_upload(USER, target["file_id"], target["version_id"], b"published")
    assert await files.download(USER, "lecture.pdf") == b"published"


async def test_empty_directories_move_delete_and_path_boundaries(client):
    await files.create_folder(USER, "empty/nested")
    assert (await files.list_files(USER, "empty"))[0]["type"] == "folder"
    await files.move(USER, "empty", "new")
    assert (await files.list_files(USER, "new"))[0]["path"] == "new/nested"
    await files.create_folder(USER, "newer")
    await files.delete(USER, ["new"])
    assert [r["path"] for r in await files.list_files(USER)] == ["newer"]
    with pytest.raises(ValueError):
        await files.upload(USER, "../outside", b"bad")
    await files.upload(USER, "newer/name", b"file")
    with pytest.raises(StorageConflict):
        await files.create_folder(USER, "newer/name/subdir")


async def test_other_user_and_wrong_file_version_are_rejected(client):
    other = uuid4()
    await db.execute("INSERT INTO users(id,email) VALUES (%s,%s)", other, f"{other}@test")
    first = await files.upload(USER, "lecture.pdf", b"secret")
    second = await files.upload(USER, "other.pdf", b"other")
    with pytest.raises(StorageNotFound):
        await files.read_version(other, first["file_id"], first["version_id"])
    with pytest.raises(StorageNotFound):
        await files.accept_upload(other, first["file_id"], first["version_id"], b"overwrite")
    with pytest.raises(StorageNotFound):
        await files.read_version(USER, second["file_id"], first["version_id"])
    assert await files.list_files(other) == []
    await files.delete(other, ["lecture.pdf"])
    assert await files.download(USER, "lecture.pdf") == b"secret"


async def test_failed_storage_does_not_change_current_version(client, monkeypatch):
    from app.storage.local import LocalStorage
    from app.storage.base import StorageUnavailable
    await files.upload(USER, "lecture.pdf", b"old")
    async def fail(*args):
        raise StorageUnavailable("unavailable")
    monkeypatch.setattr(LocalStorage, "write", fail)
    with pytest.raises(StorageUnavailable):
        await files.upload(USER, "lecture.pdf", b"new")
    assert await files.download(USER, "lecture.pdf") == b"old"


async def test_import_is_repeatable_links_index_and_keeps_sources(client, tmp_path):
    source = tmp_path / str(USER)
    (source / "COURSE/empty").mkdir(parents=True)
    (source / "COURSE/empty/.keep").touch()
    data = b"searchable old lecture"
    original = source / "COURSE/lecture.txt"
    original.write_bytes(data)
    await db.execute(
        """INSERT INTO file_index(user_id,path,size,sha256,text_content) VALUES (%s,%s,%s,%s,%s)""",
        USER, f"{USER}/COURSE/lecture.txt", len(data), hashlib.sha256(data).hexdigest(), data.decode(),
    )
    first = await import_user(USER, source)
    assert first["errors"] == [] and first["imported"] == first["linked_indexes"] == 1
    assert first["record_files"] == first["files"] == 1
    assert first["record_bytes"] == first["bytes"] == len(data)
    second = await import_user(USER, source)
    assert second["errors"] == [] and second["reused"] == 1 and second["imported"] == 0
    assert (await db.fetchrow("SELECT count(*) AS n FROM file_versions"))["n"] == 1
    assert original.read_bytes() == data
    assert await files.download(USER, "COURSE/lecture.txt") == data
    assert [r["name"] for r in await files.list_files(USER, "COURSE")] == ["empty", "lecture.txt"]
    from app.services.file_index import search
    assert (await search(USER, "searchable"))[0]["path"] == "COURSE/lecture.txt"
    index = await db.fetchrow("SELECT * FROM file_index")
    assert index["file_id"] and index["file_version_id"]
    original.unlink()
    assert any("registered source" in e["error"] for e in (await import_user(USER, source))["errors"])


async def test_import_reports_conflicts_and_missing_indexed_sources(client, tmp_path):
    source = tmp_path / "legacy"
    source.mkdir()
    (source / "lecture.txt").write_bytes(b"changed")
    for path in ("lecture.txt", "missing.txt"):
        await db.execute("INSERT INTO file_index(user_id,path,sha256) VALUES (%s,%s,'old')",
                         USER, f"{USER}/{path}")
    report = await import_user(USER, source)
    assert len(report["errors"]) == 2
    assert all(r["file_id"] is None for r in await db.fetch("SELECT file_id FROM file_index"))
    (source / "lecture.txt").write_bytes(b"changed again")
    report = await import_user(USER, source)
    assert any("conflict" in e["error"] for e in report["errors"])


async def test_http_version_upload_download_and_old_url_freeze(client, monkeypatch):
    from app.auth import _sentinel_user, require_user
    client._transport.app.dependency_overrides[require_user] = lambda: _sentinel_user()
    first = (await client.post("/api/files/upload-url", json={"path": "lecture.pdf"})).json()
    assert "file_id" in first and "object_key" not in first
    assert (await client.put(first["url"], content=b"old pdf", headers={"content-type": "application/pdf"})).status_code == 200
    old_url = (await client.get("/api/files/signed-url", params={"path": "lecture.pdf"})).json()["url"]
    second = (await client.post("/api/files/upload-url", json={"path": "lecture.pdf"})).json()
    assert first["file_id"] == second["file_id"]
    assert (await client.put(second["url"], content=b"new pdf")).status_code == 200
    assert (await client.put(first["url"], content=b"corrupt")).status_code == 409
    assert (await client.get(old_url)).content == b"old pdf"
    assert (await client.get("/api/files/raw", params={"path": "lecture.pdf"})).content == b"new pdf"
    partial = await client.get(old_url, headers={"range": "bytes=0-2"})
    assert partial.status_code == 206 and partial.content == b"old"
    assert (await client.get("/api/files/raw", params={"file_id": uuid4(), "version_id": first["version_id"]})).status_code == 404
    await files.delete(USER, ["lecture.pdf"])
    assert (await client.get(old_url)).status_code == 404


async def test_http_empty_folder_move_delete_and_sync_cutover(client):
    from app.auth import _sentinel_user, require_user
    client._transport.app.dependency_overrides[require_user] = lambda: _sentinel_user()
    assert (await client.post("/api/files/folder", json={"path": "empty/nested"})).status_code == 200
    assert (await client.post("/api/files/move", json={"from": "empty", "to": "moved", "kind": "folder"})).status_code == 200
    entries = (await client.get("/api/files/list", params={"prefix": "moved"})).json()
    assert entries[0]["name"] == "nested" and entries[0]["type"] == "folder"
    assert (await client.delete("/api/files", params={"path": "moved", "kind": "folder"})).status_code == 200
    assert (await client.get("/api/files/list")).json() == []
    assert (await client.post("/api/files/sync-materials")).status_code == 503
