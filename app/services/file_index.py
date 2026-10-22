"""Full-text indexing of database file versions; storage locations stay private."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from .. import db
from ..logging_config import operation
from . import file_records

log = logging.getLogger(__name__)
_INDEXABLE_SUFFIXES = (".pdf", ".md", ".txt", ".typ", ".ipynb")


def _extract_text(path: str, data: bytes) -> str | None:
    """Return extracted text, or None if extraction failed/unsupported."""
    if path.endswith(".pdf"):
        try:
            import fitz  # pymupdf
        except ImportError:
            log.error("index.failed", extra={"event": "index.failed", "stage": "index.extract"}, exc_info=True)
            return None
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            chunks = [page.get_text() for page in doc]
            doc.close()
            return "\n".join(chunks)
        except Exception:
            log.warning("index.failed", extra={"event": "index.failed", "stage": "index.extract"}, exc_info=True)
            return None
    if path.endswith((".md", ".txt", ".typ")):
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if path.endswith(".ipynb"):
        try:
            nb = json.loads(data.decode("utf-8", errors="ignore"))
            cells = nb.get("cells", [])
            parts: list[str] = []
            for c in cells:
                src = c.get("source", "")
                if isinstance(src, list):
                    parts.append("".join(src))
                else:
                    parts.append(str(src))
            return "\n\n".join(parts)
        except Exception:
            log.warning("index.failed", extra={"event": "index.failed", "stage": "index.extract"}, exc_info=True)
            return None
    return None



async def index_all(user_id: UUID | None = None) -> dict[str, Any]:
    """Index current completed versions. None is reserved for operator commands."""
    rows = await db.fetch(
        """SELECT f.user_id, f.id AS file_id, f.logical_path, f.course_code,
                  v.id AS version_id, v.size, v.sha256
           FROM files f JOIN file_versions v ON v.user_id = f.user_id
             AND v.file_id = f.id AND v.id = f.current_version_id
           WHERE f.deleted_at IS NULL AND v.upload_status = 'completed'
             AND (%s::uuid IS NULL OR f.user_id = %s)""", user_id, user_id,
    )
    existing = await db.fetch(
        """SELECT file_id, file_version_id FROM file_index
           WHERE file_id IS NOT NULL AND (%s::uuid IS NULL OR user_id = %s)""",
        user_id, user_id,
    )
    indexed_versions = {r["file_id"]: r["file_version_id"] for r in existing}
    stats = {"indexed": 0, "skipped": 0, "failed": 0, "pruned": 0, "total_seen": len(rows)}
    for row in rows:
        path = row["logical_path"].lower()
        if not path.endswith(_INDEXABLE_SUFFIXES) or indexed_versions.get(row["file_id"]) == row["version_id"]:
            stats["skipped"] += 1
            continue
        try:
            with operation("index.read"):
                data = await file_records.read_version(row["user_id"], row["file_id"], row["version_id"])
            with operation("index.extract"):
                text = await asyncio.to_thread(_extract_text, path, data)
            if text is None:
                stats["failed"] += 1
                continue
            async with db.db() as conn, conn.transaction():
                current = await (await conn.execute(
                    """SELECT current_version_id, deleted_at FROM files
                       WHERE user_id = %s AND id = %s FOR UPDATE""",
                    (row["user_id"], row["file_id"]),
                )).fetchone()
                if not current or current["deleted_at"] or current["current_version_id"] != row["version_id"]:
                    stats["skipped"] += 1
                    continue
                await conn.execute(
                    """DELETE FROM file_index WHERE user_id = %s AND file_id IS NULL
                       AND path = %s""",
                    (row["user_id"], f"{row['user_id']}/{row['logical_path']}"),
                )
                await conn.execute(
                    """INSERT INTO file_index(user_id, path, file_id, file_version_id,
                           course_code, size, sha256, text_content, indexed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                       ON CONFLICT (user_id, file_id) WHERE file_id IS NOT NULL
                       DO UPDATE SET path = EXCLUDED.path, file_version_id = EXCLUDED.file_version_id,
                         course_code = EXCLUDED.course_code, size = EXCLUDED.size,
                         sha256 = EXCLUDED.sha256, text_content = EXCLUDED.text_content,
                         indexed_at = EXCLUDED.indexed_at""",
                    (row["user_id"], f"{row['user_id']}/{row['file_id']}", row["file_id"],
                     row["version_id"], row["course_code"], row["size"], row["sha256"], text.replace("\x00", "")),
                )
            stats["indexed"] += 1
        except Exception:
            log.exception("index.failed", extra={"event": "index.failed", "stage": "index"})
            stats["failed"] += 1
    async with db.db() as conn:
        cursor = await conn.execute(
            """DELETE FROM file_index i USING files f
               WHERE i.user_id = f.user_id AND i.file_id = f.id AND f.deleted_at IS NOT NULL
                 AND (%s::uuid IS NULL OR i.user_id = %s)""", (user_id, user_id),
        )
        stats["pruned"] = cursor.rowcount
    return stats


async def search(user_id: UUID, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return current-version matches with the current logical name and ownership."""
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
           WHERE i.user_id = %s AND f.deleted_at IS NULL
             AND f.current_version_id = i.file_version_id AND i.search_vector @@ query.tsq
           ORDER BY rank DESC LIMIT %s""", q.strip(), user_id, limit,
    )
