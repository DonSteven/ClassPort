"""Index one immutable file version and publish only while it is current."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from .. import db
from ..logging_config import operation
from ..storage import get_storage
from ..storage.base import ObjectRef, StorageNotFound, StorageUnavailable

PIPELINE_VERSION = "1"
_INDEXABLE_SUFFIXES = (".pdf", ".md", ".txt", ".typ", ".ipynb")


class IndexingError(Exception):
    """A safe processing failure for callers and the future job runner."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _extract_text(path: str, data: bytes) -> str:
    suffix = path.lower()
    if suffix.endswith(".pdf"):
        import fitz

        try:
            with fitz.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise IndexingError("encrypted_pdf", "PDF requires a password")
                return "\n".join(page.get_text() for page in doc)
        except IndexingError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise IndexingError("invalid_pdf", "PDF text could not be extracted") from exc
    if suffix.endswith((".md", ".txt", ".typ")):
        return data.decode("utf-8", errors="ignore")
    if suffix.endswith(".ipynb"):
        try:
            notebook = json.loads(data.decode("utf-8", errors="ignore"))
            parts = []
            for cell in notebook.get("cells", []):
                source = cell.get("source", "")
                parts.append("".join(source) if isinstance(source, list) else str(source))
            return "\n\n".join(parts)
        except (ValueError, TypeError, AttributeError) as exc:
            raise IndexingError("invalid_notebook", "Notebook text could not be extracted") from exc
    raise IndexingError("unsupported_format", "File format does not support text indexing")


async def index_file(user_id: UUID, file_id: UUID, version_id: UUID, *,
                     job: dict | None = None) -> dict[str, Any]:
    """Read a fixed version without a DB checkout, then validate and publish.

    Repeated calls skip a version already indexed by this pipeline. Discarded
    results are distinct from processing failures and must not be retried as
    current work. Worker results and task state are committed together after
    checking the current execution and lease.
    """
    from . import jobs

    result = {"file_id": str(file_id), "version_id": str(version_id),
              "pipeline_version": PIPELINE_VERSION}
    try:
        # fetchrow releases its connection before storage I/O or PDF parsing.
        source = await db.fetchrow(
            """SELECT v.*, f.logical_path, f.current_version_id, f.deleted_at,
                      i.file_version_id AS indexed_version_id,
                      i.pipeline_version AS indexed_pipeline_version
               FROM file_versions v JOIN files f
                 ON f.user_id = v.user_id AND f.id = v.file_id
               LEFT JOIN file_index i ON i.user_id = f.user_id AND i.file_id = f.id
               WHERE v.user_id = %s AND v.file_id = %s AND v.id = %s AND f.kind = 'file'""",
            user_id, file_id, version_id,
        )
        if source is None:
            raise IndexingError("version_not_found", "File version not found")
        if source["upload_status"] != "completed":
            raise IndexingError("upload_incomplete", "File version upload is not completed")
        text = None
        already_indexed = (source["indexed_version_id"] == str(version_id)
                           and source["indexed_pipeline_version"] == PIPELINE_VERSION)
        if (source["deleted_at"] is None and source["current_version_id"] == str(version_id)
                and not already_indexed):
            from .file_records import protect_read
            from ..storage_config import upload_grace_seconds
            await protect_read(user_id, file_id, version_id, upload_grace_seconds(), source=source)
            with operation("index.read"):
                data = await get_storage(source["storage_backend"]).read(
                    ObjectRef(source["object_key"], source["object_version"]),
                )
            with operation("index.extract"):
                text = await asyncio.to_thread(_extract_text, source["logical_path"], data)

        async with db.db() as conn, conn.transaction():
            current = await (await conn.execute(
                """SELECT current_version_id, deleted_at, course_code FROM files
                   WHERE user_id = %s AND id = %s FOR UPDATE""", (user_id, file_id),
            )).fetchone()
            if job is not None and not await jobs.lock_execution(conn, job):
                return {**result, "status": "discarded", "reason": "execution_expired"}
            if current is None or current["deleted_at"] is not None:
                if job is not None:
                    await jobs.finish(conn, job, "cancelled", "file_deleted", "File was deleted")
                return {**result, "status": "discarded", "reason": "file_deleted"}
            if current["current_version_id"] != str(version_id):
                if job is not None:
                    await jobs.finish(conn, job, "cancelled", "version_changed", "A newer version is current")
                return {**result, "status": "discarded", "reason": "version_changed"}
            if already_indexed:
                if job is not None:
                    await jobs.finish(conn, job, "succeeded")
                return {**result, "status": "skipped"}
            await conn.execute(
                """INSERT INTO file_index(user_id, path, file_id, file_version_id,
                       course_code, size, sha256, text_content, pipeline_version, indexed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (user_id, file_id)
                   DO UPDATE SET file_version_id = EXCLUDED.file_version_id,
                     course_code = EXCLUDED.course_code, size = EXCLUDED.size,
                     sha256 = EXCLUDED.sha256, text_content = EXCLUDED.text_content,
                     pipeline_version = EXCLUDED.pipeline_version, indexed_at = EXCLUDED.indexed_at""",
                (user_id, f"{user_id}/{file_id}", file_id, version_id, current["course_code"],
                 source["size"], source["sha256"], text.replace("\x00", ""), PIPELINE_VERSION),
            )
            if job is not None:
                await jobs.finish(conn, job, "succeeded")
        return {**result, "status": "indexed"}
    except jobs.ExecutionExpired:
        return {**result, "status": "discarded", "reason": "execution_expired"}
    except StorageNotFound as exc:
        raise IndexingError("content_missing", "Stored file content is missing") from exc
    except StorageUnavailable as exc:
        raise IndexingError("storage_unavailable", "File storage is unavailable", retryable=True) from exc
    except (OperationalError, PoolTimeout) as exc:
        raise IndexingError("database_unavailable", "Database is unavailable", retryable=True) from exc


async def search(user_id: UUID, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return only current-version matches using current logical metadata."""
    if len(q.strip()) < 2:
        return []
    return await db.fetch(
        """WITH query AS (SELECT websearch_to_tsquery('simple', %s) AS tsq)
           SELECT f.logical_path AS path, f.course_code, i.size,
                  f.id AS file_id, i.file_version_id AS version_id,
                  ts_rank(i.search_vector, query.tsq) AS rank,
                  ts_headline('simple', i.text_content, query.tsq,
                    'StartSel=<<,StopSel=>>,MaxFragments=2,MaxWords=15,MinWords=5,FragmentDelimiter=" … "') AS snippet
           FROM file_index i JOIN files f ON f.user_id = i.user_id AND f.id = i.file_id, query
           WHERE i.user_id = %s AND f.kind = 'file' AND f.deleted_at IS NULL
             AND f.current_version_id = i.file_version_id AND i.search_vector @@ query.tsq
           ORDER BY rank DESC LIMIT %s""", q.strip(), user_id, limit,
    )
