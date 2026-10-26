"""User-owned logical files and immutable versions, independent of storage paths."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import logging
import mimetypes
from typing import Any
from uuid import UUID, uuid4

from .. import db
from ..schemas import EventCreate
from ..storage import get_storage
from ..storage.base import ObjectRef, StorageConflict, StorageNotFound
from ..storage_config import upload_grace_seconds
from . import events as events_svc
from . import file_index as index_svc
from . import jobs as jobs_svc

log = logging.getLogger(__name__)


class InvalidFilePath(ValueError):
    pass


def clean_path(path: str, *, allow_root: bool = False) -> str:
    parts = [part for part in path.strip().split("/") if part]
    if (not parts and not allow_root) or any(
        part in (".", "..") or "\\" in part or "\x00" in part for part in parts
    ):
        raise InvalidFilePath("invalid file path")
    return "/".join(parts)


async def _lock_tree(conn, user_id: UUID) -> None:
    # Serialize this user's path mutations, including subtree moves and uploads.
    row = await (await conn.execute(
        "SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,),
    )).fetchone()
    if row is None:
        raise StorageNotFound("file not found")


async def _course(conn, user_id: UUID, path: str) -> str | None:
    if "/" not in path:
        return None
    folder = path.split("/", 1)[0]
    row = await (await conn.execute(
        """SELECT code FROM courses WHERE user_id = %s
           AND (code = %s OR folder_name = %s) ORDER BY code LIMIT 1""",
        (user_id, folder, folder),
    )).fetchone()
    return row["code"] if row else None


async def _ensure(conn, user_id: UUID, path: str, kind: str) -> dict:
    row = await (await conn.execute(
        """INSERT INTO files(user_id, logical_path, kind, course_code)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (user_id, logical_path) WHERE deleted_at IS NULL
           DO UPDATE SET logical_path = EXCLUDED.logical_path RETURNING *""",
        (user_id, path, kind, await _course(conn, user_id, path)),
    )).fetchone()
    if row["kind"] != kind:
        raise StorageConflict("a file or directory already uses this path")
    return row


async def _parents(conn, user_id: UUID, path: str) -> None:
    parts = path.split("/")
    for end in range(1, len(parts)):
        await _ensure(conn, user_id, "/".join(parts[:end]), "directory")


async def _log(user_id: UUID, kind: str, payload: dict[str, Any]) -> None:
    try:
        path = payload.get("path") or next(iter(payload.get("paths", [])), "")
        async with db.db() as conn:
            course = await _course(conn, user_id, path)
        await events_svc.record_event(
            user_id, EventCreate(kind=kind, course_code=course, payload=payload),
        )
    except Exception:
        log.exception("storage.audit.failed", extra={"event": "storage.audit.failed"})


async def create_folder(user_id: UUID, path: str) -> dict:
    path = clean_path(path)
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        await _parents(conn, user_id, path)
        row = await _ensure(conn, user_id, path, "directory")
    await _log(user_id, "storage:folder", {"path": path})
    return {"folder": path, "file_id": row["id"]}


async def list_files(user_id: UUID, prefix: str = "", *, limit: int = 200) -> list[dict]:
    prefix = clean_path(prefix, allow_root=True)
    start = prefix + "/" if prefix else ""
    rows = await db.fetch(
        """SELECT f.*, v.size, v.content_type, j.id AS job_id, j.status AS job_status,
                  j.error_code AS job_error_code, j.last_error AS job_error,
                  (i.file_version_id = f.current_version_id
                   AND i.pipeline_version = %s AND (j.id IS NULL OR j.status = 'succeeded'))
                     AS searchable
           FROM files f
           LEFT JOIN file_versions v ON v.user_id = f.user_id
             AND v.file_id = f.id AND v.id = f.current_version_id
           LEFT JOIN file_index i ON i.user_id = f.user_id AND i.file_id = f.id
           LEFT JOIN LATERAL (SELECT id, status, error_code, last_error FROM jobs
               WHERE user_id = f.user_id AND file_id = f.id
                 AND file_version_id = f.current_version_id AND job_type = 'index_file'
                 AND pipeline_version = %s ORDER BY created_at DESC LIMIT 1) j ON true
           WHERE f.user_id = %s AND f.deleted_at IS NULL
             AND left(f.logical_path, length(%s)) = %s
             AND strpos(substr(f.logical_path, length(%s) + 1), '/') = 0
             AND f.logical_path <> %s
           ORDER BY (f.kind = 'directory') DESC, lower(f.logical_path), f.logical_path
           LIMIT %s""", index_svc.PIPELINE_VERSION, index_svc.PIPELINE_VERSION,
        user_id, start, start, start, prefix, limit,
    )
    out = []
    for row in rows:
        name = row["logical_path"].rsplit("/", 1)[-1]
        if name.startswith("."):
            continue
        out.append({
            "name": name, "path": row["logical_path"],
            "type": "folder" if row["kind"] == "directory" else "file",
            "file_id": row["id"], "version_id": row["current_version_id"],
            "size": row["size"], "content_type": row["content_type"],
            "updated_at": row["updated_at"].isoformat(),
            "job_id": row["job_id"], "job_status": row["job_status"],
            "job_error_code": row["job_error_code"], "job_error": row["job_error"],
            "job_can_retry": row["job_status"] == "failed" and row["job_error_code"]
                in jobs_svc.RETRYABLE | {"unsupported_pipeline"},
            "searchable": bool(row["searchable"]),
        })
    return out


async def list_recursive(user_id: UUID, prefix: str) -> list[str]:
    prefix = clean_path(prefix, allow_root=True)
    start = prefix + "/" if prefix else ""
    rows = await db.fetch(
        """SELECT logical_path FROM files WHERE user_id = %s AND deleted_at IS NULL
           AND kind = 'file' AND current_version_id IS NOT NULL
           AND left(logical_path, length(%s)) = %s ORDER BY logical_path""",
        user_id, start, start,
    )
    return [r["logical_path"] for r in rows
            if not any(p.startswith(".") for p in r["logical_path"].split("/"))]


async def begin_upload(user_id: UUID, path: str, *, size: int | None = None,
                       sha256: str | None = None, content_type: str | None = None) -> dict:
    path = clean_path(path)
    backend = os.environ.get("STORAGE_BACKEND", "local")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 5 * 1024**3):
        raise InvalidFilePath("invalid upload size")
    if sha256 is not None and (not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)):
        raise InvalidFilePath("invalid SHA-256 checksum")
    if backend == "s3" and (size is None or sha256 is None):
        raise InvalidFilePath("S3 uploads require size and SHA-256 checksum")
    mime = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
    if not isinstance(mime, str) or len(mime) > 255 or any(c in mime for c in "\r\n\0"):
        raise InvalidFilePath("invalid content type")
    store = get_storage(backend)
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        await _parents(conn, user_id, path)
        file = await _ensure(conn, user_id, path, "file")
        version_id = str(uuid4())
        key = f"objects/{user_id}/{file['id']}/{version_id}/content"
        version = await (await conn.execute(
            """INSERT INTO file_versions(id, user_id, file_id, revision, object_key,
                      storage_backend, size, sha256, content_type)
               SELECT %s, %s, %s, coalesce(max(revision), 0) + 1, %s, %s, %s, %s, %s
               FROM file_versions WHERE user_id = %s AND file_id = %s RETURNING *""",
            (version_id, user_id, file["id"], key, backend, size, sha256, mime, user_id, file["id"]),
        )).fetchone()
        target = await asyncio.to_thread(store.upload_url, ObjectRef(key), file_id=file["id"],
                                         version_id=version["id"], content_type=mime, sha256=sha256)
        await conn.execute(
            "UPDATE file_versions SET upload_expires_at=clock_timestamp()+%s*interval '1 second' WHERE id=%s",
            (target['expires_in'], version_id),
        )
    return {"path": path, "file_id": file["id"], "version_id": version["id"],
            "storage_backend": backend, **target}


async def accept_upload(user_id: UUID, file_id: UUID, version_id: UUID,
                        data: bytes, content_type: str | None = None) -> dict:
    """Stage immutable bytes; completion is an explicit, idempotent operation."""
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        row = await (await conn.execute(
            """SELECT v.*, f.logical_path FROM file_versions v JOIN files f
                 ON f.user_id = v.user_id AND f.id = v.file_id
               WHERE v.user_id = %s AND v.file_id = %s AND v.id = %s
                 AND f.deleted_at IS NULL FOR UPDATE OF v, f""",
            (user_id, file_id, version_id),
        )).fetchone()
        if row is None:
            raise StorageNotFound("file version not found")
        if row["upload_status"] != "pending":
            raise StorageConflict("file version is already completed or abandoned")
        valid = await (await conn.execute(
            'SELECT upload_expires_at>clock_timestamp() AS valid FROM file_versions WHERE id=%s', (version_id,),
        )).fetchone()
        if not valid['valid']:
            raise StorageConflict('upload authorization expired')
        mime = content_type or mimetypes.guess_type(row["logical_path"])[0]
        mime = mime or "application/octet-stream"
        store = get_storage(row["storage_backend"])
        ref = ObjectRef(row["object_key"], row["object_version"])
        digest = hashlib.sha256(data).hexdigest()
        if row["storage_backend"] == "s3" and (row["size"] != len(data) or row["sha256"] != digest):
            raise StorageConflict("content differs from the requested upload")
        # Immutable publication also permits finishing an interrupted PUT if its
        # bytes were published before the database transaction committed.
        try:
            ref = await store.write(ref, data, content_type=mime)
        except StorageConflict:
            if await store.read(ref) != data:
                raise StorageConflict("file version already contains different content") from None
        if (await store.stat(ref)).size != len(data):
            raise StorageConflict("stored content size differs from upload")
        await conn.execute(
            """UPDATE file_versions SET size = %s, sha256 = %s, content_type = %s, object_version = %s
               WHERE user_id = %s AND file_id = %s AND id = %s""",
            (len(data), digest, mime, ref.version, user_id, file_id, version_id),
        )
    return {"path": row["logical_path"], "file_id": str(file_id),
            "version_id": str(version_id), "size": len(data), "content_type": mime}


async def complete_upload(user_id: UUID, file_id: UUID, version_id: UUID,
                          object_version: str | None = None) -> dict:
    """Verify immutable content without a DB checkout, then publish atomically."""
    source = await db.fetchrow(
        """SELECT v.*, f.logical_path FROM file_versions v JOIN files f
             ON f.user_id = v.user_id AND f.id = v.file_id
           WHERE v.user_id = %s AND v.file_id = %s AND v.id = %s
             AND f.deleted_at IS NULL""", user_id, file_id, version_id,
    )
    if source is None:
        raise StorageNotFound("file version not found")
    if source['content_state'] != 'available':
        raise StorageConflict('file content was retired')
    candidate = source["object_version"] or object_version
    if object_version and source["object_version"] and object_version != source["object_version"]:
        raise StorageConflict("file version is already bound to different content")
    if source["upload_status"] == "abandoned":
        raise StorageConflict("upload was abandoned")
    if source["upload_status"] == "pending":
        if source["size"] is None or source["sha256"] is None:
            raise StorageConflict("upload bytes have not been received")
        store = get_storage(source["storage_backend"])
        ref = ObjectRef(source["object_key"], candidate)
        try:
            info = await store.stat(ref)
            if source["storage_backend"] == "local":
                data = await store.read(ref)
                digest = hashlib.sha256(data).hexdigest()
            else:
                digest = info.sha256
                if info.content_type != source["content_type"]:
                    raise StorageConflict("stored content type differs from upload")
            if info.size != source["size"] or digest != source["sha256"]:
                raise StorageConflict("stored content differs from the requested upload")
        except StorageNotFound:
            raise StorageConflict("upload bytes have not been received") from None
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        row = await (await conn.execute(
            """SELECT v.*, f.logical_path FROM file_versions v JOIN files f
                 ON f.user_id = v.user_id AND f.id = v.file_id
               WHERE v.user_id = %s AND v.file_id = %s AND v.id = %s
                 AND f.deleted_at IS NULL FOR UPDATE OF v, f""",
            (user_id, file_id, version_id),
        )).fetchone()
        if row is None:
            raise StorageNotFound("file version not found")
        if row["upload_status"] == "abandoned":
            raise StorageConflict("upload was abandoned")
        if row['content_state'] != 'available':
            raise StorageConflict('file content was retired')
        if row["object_version"] and candidate != row["object_version"]:
            raise StorageConflict("file version is already bound to different content")
        if row["upload_status"] == "pending":
            if (row["size"], row["sha256"]) != (source["size"], source["sha256"]):
                raise StorageConflict("upload changed during confirmation")
            await conn.execute(
                """UPDATE file_versions SET upload_status = 'completed', completed_at = now(),
                       object_version = %s
                   WHERE user_id = %s AND file_id = %s AND id = %s""",
                (candidate, user_id, file_id, version_id),
            )
        # An earlier upload finishing late cannot replace a newer revision.
        await conn.execute(
            """UPDATE files f SET current_version_id = %s, updated_at = now()
               WHERE f.user_id = %s AND f.id = %s AND (f.current_version_id IS NULL OR
                 (SELECT revision FROM file_versions WHERE user_id = %s
                  AND file_id = %s AND id = f.current_version_id) < %s)""",
            (version_id, user_id, file_id, user_id, file_id, row["revision"]),
        )
        job = await jobs_svc.create(conn, user_id, file_id, version_id)
        await conn.execute(
            """UPDATE jobs j SET status = 'cancelled', error_code = 'version_changed',
                 last_error = 'A newer version is current', finished_at = now(), updated_at = now()
               FROM files f WHERE j.user_id = %s AND j.file_id = %s
                 AND f.user_id = j.user_id AND f.id = j.file_id
                 AND j.file_version_id <> f.current_version_id
                 AND j.job_type='index_file'
                 AND j.status IN ('queued', 'retry_wait')""", (user_id, file_id),
        )
        from . import storage_cleanup
        retired = await (await conn.execute(
            """UPDATE file_versions v SET retired_at=coalesce(retired_at,clock_timestamp()) FROM files f
               WHERE v.user_id=%s AND v.file_id=%s AND f.user_id=v.user_id AND f.id=v.file_id
                 AND v.id<>f.current_version_id AND v.upload_status='completed'
               RETURNING v.*,f.deleted_at""", (user_id, file_id),
        )).fetchall()
        for old in retired:
            await storage_cleanup.schedule_content(conn, old)
        job = await (await conn.execute(
            "SELECT * FROM jobs WHERE user_id = %s AND id = %s", (user_id, job["id"]),
        )).fetchone()
    result = {"path": row["logical_path"], "file_id": str(file_id),
              "version_id": str(version_id), "size": row["size"],
              "content_type": row["content_type"], "job_id": job["id"], "job_status": job["status"]}
    await _log(user_id, "storage:upload", result)
    return result


async def upload(user_id: UUID, path: str, data: bytes, content_type: str | None = None) -> dict:
    target = await begin_upload(user_id, path, size=len(data),
                                sha256=hashlib.sha256(data).hexdigest(), content_type=content_type)
    await accept_upload(user_id, target["file_id"], target["version_id"], data, content_type)
    return await complete_upload(user_id, target["file_id"], target["version_id"])


async def get_version(user_id: UUID, file_id: UUID, version_id: UUID) -> dict:
    row = await db.fetchrow(
        """SELECT v.*, f.logical_path, f.course_code FROM file_versions v JOIN files f
             ON f.user_id = v.user_id AND f.id = v.file_id
           WHERE v.user_id = %s AND v.file_id = %s AND v.id = %s
             AND v.upload_status = 'completed' AND v.content_state='available' AND f.deleted_at IS NULL""",
        user_id, file_id, version_id,
    )
    if row is None:
        raise StorageNotFound("file version not found")
    return row


async def current_version(user_id: UUID, path: str) -> dict:
    path = clean_path(path)
    row = await db.fetchrow(
        """SELECT v.*, f.logical_path, f.course_code FROM files f JOIN file_versions v
             ON v.user_id = f.user_id AND v.file_id = f.id AND v.id = f.current_version_id
           WHERE f.user_id = %s AND f.logical_path = %s AND f.deleted_at IS NULL
             AND v.upload_status = 'completed' AND v.content_state='available'""", user_id, path,
    )
    if row is None:
        raise StorageNotFound("file not found")
    return row


async def read_version(user_id: UUID, file_id: UUID, version_id: UUID) -> bytes:
    row = await get_version(user_id, file_id, version_id)
    await protect_read(user_id, file_id, version_id, upload_grace_seconds(), source=row)
    data = await get_storage(row["storage_backend"]).read(
        ObjectRef(row["object_key"], row["object_version"]),
    )
    await _log(user_id, "storage:read", {"path": row["logical_path"], "size": len(data)})
    return data


async def download(user_id: UUID, path: str) -> bytes:
    row = await current_version(user_id, path)
    return await read_version(user_id, row["file_id"], row["id"])


async def stat(user_id: UUID, path: str) -> dict | None:
    try:
        row = await current_version(user_id, path)
    except StorageNotFound:
        return None
    return {"path": row["logical_path"], "file_id": row["file_id"], "version_id": row["id"],
            "size": row["size"], "mimetype": row["content_type"],
            "updated_at": row["completed_at"].isoformat()}


async def exists(user_id: UUID, path: str) -> bool:
    return await stat(user_id, path) is not None


async def signed_url(user_id: UUID, path: str, expires_in: int = 3600) -> dict:
    row = await current_version(user_id, path)
    return await version_url(user_id, row, expires_in)


async def protect_read(user_id, file_id, version_id, seconds, *, source=None):
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        row = await (await conn.execute(
            """UPDATE file_versions v SET read_expires_at=greatest(read_expires_at,
                 clock_timestamp()+%s*interval '1 second') FROM files f
               WHERE v.user_id=%s AND v.file_id=%s AND v.id=%s AND f.user_id=v.user_id AND f.id=v.file_id
                 AND f.deleted_at IS NULL AND v.content_state='available' RETURNING v.id""",
            (seconds, user_id, file_id, version_id),
        )).fetchone()
        if row is None:
            raise StorageNotFound('file version not found')
        if source and source['storage_backend'] == 'local':
            await conn.execute(
                """UPDATE storage_migrations SET retain_until=greatest(retain_until,
                     clock_timestamp()+%s*interval '1 second')
                   WHERE user_id=%s AND file_version_id=%s AND source_key=%s AND status='completed'""",
                (seconds, user_id, version_id, source['object_key']),
            )


async def version_url(user_id, row, expires_in=3600):
    target = await asyncio.to_thread(
        get_storage(row["storage_backend"]).download_url,
        ObjectRef(row["object_key"], row["object_version"]), file_id=row["file_id"],
        version_id=row["id"], expires_in=expires_in,
        filename=row["logical_path"].rsplit("/", 1)[-1], content_type=row["content_type"],
    )
    await protect_read(user_id, row['file_id'], row['id'], target['expires_in'] + upload_grace_seconds(), source=row)
    return target


async def delete(user_id: UUID, paths: list[str]) -> dict:
    deleted = []
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        for path in paths:
            path = clean_path(path)
            start = path + "/"
            rows = await (await conn.execute(
                """UPDATE files SET deleted_at = now(), updated_at = now()
                   WHERE user_id = %s AND deleted_at IS NULL
                     AND (logical_path = %s OR left(logical_path, length(%s)) = %s)
                   RETURNING logical_path""", (user_id, path, start, start),
            )).fetchall()
            deleted.extend(r["logical_path"] for r in rows)
        await conn.execute(
            """UPDATE jobs j SET status = 'cancelled', error_code = 'file_deleted',
                 last_error = 'File was deleted', finished_at = now(), updated_at = now()
               FROM files f WHERE j.user_id = %s AND f.user_id = j.user_id
                 AND f.id = j.file_id AND f.deleted_at IS NOT NULL
                 AND j.job_type='index_file'
                 AND j.status IN ('queued', 'retry_wait')""", (user_id,),
        )
        from . import storage_cleanup
        versions = await (await conn.execute(
            """SELECT v.*,f.deleted_at FROM file_versions v JOIN files f
               ON f.user_id=v.user_id AND f.id=v.file_id
               WHERE v.user_id=%s AND f.deleted_at IS NOT NULL""", (user_id,),
        )).fetchall()
        for version in versions:
            await storage_cleanup.schedule_content(conn, version)
    await _log(user_id, "storage:delete", {"paths": paths, "count": len(deleted)})
    return {"deleted": sorted(set(deleted))}


async def move(user_id: UUID, source: str, destination: str) -> dict:
    source, destination = clean_path(source), clean_path(destination)
    if source == destination:
        return {"moved": []}
    if destination.startswith(source + "/"):
        raise StorageConflict("cannot move a directory inside itself")
    async with db.db() as conn, conn.transaction():
        await _lock_tree(conn, user_id)
        start = source + "/"
        rows = await (await conn.execute(
            """SELECT id, logical_path FROM files WHERE user_id = %s AND deleted_at IS NULL
               AND (logical_path = %s OR left(logical_path, length(%s)) = %s)
               ORDER BY length(logical_path)""", (user_id, source, start, start),
        )).fetchall()
        if not rows or rows[0]["logical_path"] != source:
            raise StorageNotFound("file not found")
        target = await (await conn.execute(
            "SELECT id FROM files WHERE user_id = %s AND logical_path = %s AND deleted_at IS NULL",
            (user_id, destination),
        )).fetchone()
        if target:
            raise StorageConflict("destination already exists")
        await _parents(conn, user_id, destination)
        for row in rows:
            path = destination + row["logical_path"][len(source):]
            await conn.execute(
                """UPDATE files SET logical_path = %s, course_code = %s, updated_at = now()
                   WHERE user_id = %s AND id = %s""",
                (path, await _course(conn, user_id, path), user_id, row["id"]),
            )
    await _log(user_id, "storage:move", {"path": source, "to": destination})
    return {"moved": [{"from": r["logical_path"],
                       "to": destination + r["logical_path"][len(source):]} for r in rows]}
