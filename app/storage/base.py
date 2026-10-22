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


class Storage(Protocol):
    async def read(self, ref: ObjectRef) -> bytes: ...
    async def write(self, ref: ObjectRef, data: bytes) -> None: ...
    async def stat(self, ref: ObjectRef) -> int: ...
    async def delete(self, ref: ObjectRef) -> None: ...
    async def check_readable(self) -> None: ...
    def upload_url(self, ref: ObjectRef, *, file_id: str, version_id: str) -> str: ...
    def download_url(self, ref: ObjectRef, *, file_id: str, version_id: str,
                     expires_in: int = 3600) -> str: ...
