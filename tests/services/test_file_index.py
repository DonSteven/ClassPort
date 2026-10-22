"""Index completed immutable versions and search through current file metadata."""
import json
from uuid import uuid4

import pytest

from app import db
from app.auth import SENTINEL_USER_ID as USER
from app.services import file_index as index
from app.services import file_records as files
from app.storage import get_storage
from app.storage.base import ObjectRef


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
    stats = await index.index_all(USER)
    assert stats["indexed"] == 1 and stats["failed"] == 0
    result = await index.search(USER, word)
    assert result[0]["path"] == path and result[0]["file_id"] == version["file_id"]
    assert result[0]["version_id"] == version["version_id"]
    assert "<<" in result[0]["snippet"]
    assert (await index.index_all(USER))["skipped"] == 1


async def test_search_tracks_rename_and_hides_replaced_or_deleted_version(client):
    await files.upload(USER, "course/lecture.txt", b"quantum mechanics")
    await index.index_all(USER)
    await files.move(USER, "course", "renamed")
    assert (await index.search(USER, "quantum"))[0]["path"] == "renamed/lecture.txt"
    await files.upload(USER, "renamed/lecture.txt", b"relativity principles")
    assert await index.search(USER, "quantum") == []
    await index.index_all(USER)
    assert (await index.search(USER, "relativity"))[0]["path"] == "renamed/lecture.txt"
    await files.delete(USER, ["renamed"])
    assert await index.search(USER, "relativity") == []
    assert (await index.index_all(USER))["pruned"] == 1


async def test_failed_pdf_and_missing_object_do_not_clear_other_indexes(client):
    await files.upload(USER, "valid.txt", b"quantum mechanics")
    await index.index_all(USER)
    await files.upload(USER, "broken.pdf", b"not a pdf")
    version = await files.upload(USER, "missing.txt", b"missing content")
    row = await files.get_version(USER, version["file_id"], version["version_id"])
    await get_storage().delete(ObjectRef(row["object_key"]))
    stats = await index.index_all(USER)
    assert stats["failed"] == 2 and stats["pruned"] == 0
    assert len(await index.search(USER, "quantum")) == 1


async def test_index_and_search_are_user_scoped(client):
    other = uuid4()
    await db.execute("INSERT INTO users(id,email) VALUES (%s,%s)", other, f"{other}@test")
    await files.upload(USER, "same.txt", b"quantum physics")
    await files.upload(other, "same.txt", b"quantum chemistry")
    assert (await index.index_all(USER))["indexed"] == 1
    assert await index.search(other, "quantum") == []
    assert (await index.index_all())["indexed"] == 1
    assert len(await index.search(other, "quantum")) == 1
    assert "chemistry" not in str(await index.search(USER, "quantum"))


async def test_empty_short_query_unsupported_and_nuls(client):
    assert (await index.index_all(USER))["total_seen"] == 0
    await files.upload(USER, "image.png", b"not indexed")
    await files.upload(USER, "notes.txt", b"quantum\x00 mechanics")
    stats = await index.index_all(USER)
    assert stats["indexed"] == stats["skipped"] == 1
    assert await index.search(USER, "q") == []
    assert await index.search(USER, "nonsenseword") == []
    assert len(await index.search(USER, "quantum", limit=1)) == 1


async def test_rebuild_replaces_stale_legacy_index_after_import(client, tmp_path):
    from scripts.import_local_files import import_user
    source = tmp_path / "legacy"
    source.mkdir()
    (source / "lecture.txt").write_bytes(b"quantum mechanics")
    await db.execute(
        "INSERT INTO file_index(user_id,path,size,sha256,text_content) VALUES (%s,%s,1,'old','stale')",
        USER, f"{USER}/lecture.txt",
    )
    assert (await import_user(USER, source))["errors"]
    assert (await index.index_all(USER))["indexed"] == 1
    assert (await import_user(USER, source))["errors"] == []
    assert (await db.fetchrow("SELECT count(*) AS n FROM file_index"))["n"] == 1
