"""File operations shared by authenticated REST and MCP callers."""
from typing import Any
from uuid import UUID

from ..services import file_index as file_index_svc
from ..services import file_records as svc


async def list_files(
    user_id: UUID, prefix: str = "", *, limit: int = 200
) -> list[dict[str, Any]]:
    return await svc.list_files(user_id, prefix, limit=limit)


async def list_recursive(user_id: UUID, prefix: str) -> list[str]:
    return await svc.list_recursive(user_id, prefix)


async def download(user_id: UUID, path: str) -> bytes:
    return await svc.download(user_id, path)


async def exists(user_id: UUID, path: str) -> bool:
    return await svc.exists(user_id, path)


async def stat(user_id: UUID, path: str) -> dict[str, Any] | None:
    return await svc.stat(user_id, path)


async def upload(
    user_id: UUID,
    path: str,
    data: bytes,
    content_type: str | None = None,
) -> dict[str, Any]:
    return await svc.upload(user_id, path, data, content_type)


async def delete(user_id: UUID, paths: list[str]) -> dict[str, Any]:
    return await svc.delete(user_id, paths)


async def signed_url(user_id: UUID, path: str, expires_in: int = 3600) -> str:
    return await svc.signed_url(user_id, path, expires_in)


async def signed_upload_url(user_id: UUID, path: str) -> dict[str, Any]:
    return await svc.begin_upload(user_id, path)


async def move(user_id: UUID, source: str, destination: str) -> dict[str, Any]:
    return await svc.move(user_id, source, destination)


async def search(user_id: UUID, q: str, limit: int = 20) -> list[dict[str, Any]]:
    return await file_index_svc.search(user_id, q, limit)


async def index_file(user_id: UUID, file_id: UUID, version_id: UUID) -> dict[str, Any]:
    return await file_index_svc.index_file(user_id, file_id, version_id)


async def index_user(user_id: UUID) -> dict[str, Any]:
    return await file_index_svc.index_user(user_id)


async def create_folder(user_id: UUID, path: str) -> dict:
    return await svc.create_folder(user_id, path)


async def accept_upload(user_id: UUID, file_id: UUID, version_id: UUID,
                        data: bytes, content_type: str | None = None) -> dict:
    return await svc.accept_upload(user_id, file_id, version_id, data, content_type)


async def current_version(user_id: UUID, path: str) -> dict:
    return await svc.current_version(user_id, path)


async def get_version(user_id: UUID, file_id: UUID, version_id: UUID) -> dict:
    return await svc.get_version(user_id, file_id, version_id)


async def read_version(user_id: UUID, file_id: UUID, version_id: UUID) -> bytes:
    return await svc.read_version(user_id, file_id, version_id)
