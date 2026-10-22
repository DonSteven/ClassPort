"""Local content operations, immutability and translated storage failures."""
import asyncio

import pytest

from app.storage.base import ObjectRef, StorageConflict, StorageNotFound, StorageUnavailable
from app.storage.local import LocalStorage


async def test_local_round_trip_and_delete(tmp_path):
    store = LocalStorage(tmp_path)
    ref = ObjectRef("objects/user/file/version/content")
    await store.write(ref, b"PDF data")
    assert await store.read(ref) == b"PDF data"
    assert await store.stat(ref) == 8
    await store.delete(ref)
    await store.delete(ref)
    with pytest.raises(StorageNotFound):
        await store.read(ref)


async def test_concurrent_writes_cannot_replace_published_content(tmp_path):
    store, ref = LocalStorage(tmp_path), ObjectRef("objects/version/content")
    results = await asyncio.gather(store.write(ref, b"first"), store.write(ref, b"second"),
                                   return_exceptions=True)
    assert sum(isinstance(r, StorageConflict) for r in results) == 1
    assert await store.read(ref) in (b"first", b"second")


@pytest.mark.parametrize("key", ["../outside", "/etc/passwd", ""])
async def test_local_rejects_escape(tmp_path, key):
    with pytest.raises(StorageConflict):
        await LocalStorage(tmp_path).read(ObjectRef(key))


async def test_local_maps_permission_error(tmp_path, monkeypatch):
    from pathlib import Path
    def denied(*args):
        raise PermissionError("private disk location")
    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(StorageUnavailable, match="unavailable"):
        await LocalStorage(tmp_path).read(ObjectRef("objects/content"))
