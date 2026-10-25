"""Immutable local objects under STUDY_ROOT, addressed by internal keys."""
import asyncio
import os
from pathlib import Path
import tempfile

from .base import ObjectInfo, ObjectRef, StorageConflict, StorageNotFound, StorageUnavailable


class LocalStorage:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(os.environ.get("STUDY_ROOT", "/opt/courses"))

    def _resolve(self, ref: ObjectRef) -> Path:
        root = self.root.resolve()
        path = (root / ref.key).resolve()
        if ref.version is not None or not ref.key or path == root or not path.is_relative_to(root):
            raise StorageConflict("invalid local object reference")
        return path

    async def _run(self, action, *args):
        try:
            return await asyncio.to_thread(action, *args)
        except FileNotFoundError as exc:
            raise StorageNotFound("stored content not found") from exc
        except FileExistsError as exc:
            raise StorageConflict("stored content is immutable") from exc
        except OSError as exc:
            raise StorageUnavailable("local storage is unavailable") from exc

    async def read(self, ref: ObjectRef) -> bytes:
        return await self._run(self._resolve(ref).read_bytes)

    def _write(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            name = Path(temporary.name)
            try:
                temporary.write(data)
                temporary.flush()
                # Linking publishes the complete object atomically and cannot replace
                # an existing version, including from a repeated/concurrent PUT.
                os.link(name, target)
            finally:
                name.unlink(missing_ok=True)

    async def write(self, ref: ObjectRef, data: bytes, *,
                    content_type: str = "application/octet-stream") -> ObjectRef:
        await self._run(self._write, self._resolve(ref), data)
        return ref

    async def stat(self, ref: ObjectRef) -> ObjectInfo:
        return ObjectInfo(ref, (await self._run(self._resolve(ref).stat)).st_size)

    async def delete(self, ref: ObjectRef) -> None:
        target = self._resolve(ref)
        await self._run(lambda: target.unlink(missing_ok=True))

    def _check_readable(self) -> None:
        with os.scandir(self.root) as entries:
            next(entries, None)

    async def check_readable(self) -> None:
        await self._run(self._check_readable)

    def upload_url(self, ref: ObjectRef, *, file_id: str, version_id: str,
                   content_type: str, sha256: str | None) -> dict:
        return {"url": f"/api/files/upload-target?file_id={file_id}&version_id={version_id}",
                "headers": {}, "expires_in": 3600}

    def download_url(self, ref: ObjectRef, *, file_id: str, version_id: str,
                     expires_in: int = 3600, filename: str = "",
                     content_type: str = "application/octet-stream") -> dict:
        return {"url": f"/api/files/raw?file_id={file_id}&version_id={version_id}",
                "expires_in": expires_in}
