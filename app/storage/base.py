"""Small content interface shared by file readers and upload handlers."""
from dataclasses import dataclass
from typing import Protocol


class StorageNotFound(FileNotFoundError):
    pass


class StorageConflict(ValueError):
    pass


class StorageUnavailable(OSError):
    pass


@dataclass(frozen=True)
class ObjectRef:
    key: str
    version: str | None = None


@dataclass(frozen=True)
class ObjectInfo:
    ref: ObjectRef
    size: int
    sha256: str | None = None
    content_type: str | None = None


class Storage(Protocol):
    async def read(self, ref: ObjectRef) -> bytes: ...
    async def write(self, ref: ObjectRef, data: bytes, *,
                    content_type: str = "application/octet-stream") -> ObjectRef: ...
    async def stat(self, ref: ObjectRef) -> ObjectInfo: ...
    async def delete(self, ref: ObjectRef) -> None: ...
    async def check_readable(self) -> None: ...
    def upload_url(self, ref: ObjectRef, *, file_id: str, version_id: str,
                   content_type: str, sha256: str | None) -> dict: ...
    def download_url(self, ref: ObjectRef, *, file_id: str, version_id: str,
                     expires_in: int = 3600, filename: str = "",
                     content_type: str = "application/octet-stream") -> dict: ...
