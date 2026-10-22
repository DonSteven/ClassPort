"""Authenticated logical-file API; content locations are private to storage."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from .. import db
from ..auth import require_user, User
from ..intents import files as intent


def _sanitize_path(p: str) -> str:
    s = unicodedata.normalize("NFC", p)
    return "/".join(seg for seg in s.split("/") if seg)


def _safe_key(raw: str) -> str:
    cleaned = raw.strip().strip("/")
    if not cleaned or ".." in cleaned:
        raise HTTPException(400, "invalid path")
    return cleaned


router = APIRouter(prefix="/files", tags=["files"], dependencies=[Depends(require_user)])
_INLINE_MIMETYPES = frozenset({
    "application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp",
})


@router.get("/list")
async def list_files(prefix: str = "", limit: int = Query(500, ge=1, le=1000),
                     user: User = Depends(require_user)) -> list[dict[str, Any]]:
    return await intent.list_files(user.id, prefix, limit=limit)


@router.get("/signed-url")
async def signed_url(path: str, expires_in: int = Query(3600, ge=60, le=86400),
                     user: User = Depends(require_user)) -> dict:
    # Local URLs use the authenticated session; the URL pins the resolved version.
    return {"url": await intent.signed_url(user.id, path, expires_in), "expires_in": expires_in}


@router.post("/upload-url")
async def upload_url(body: dict = Body(...), user: User = Depends(require_user)) -> dict:
    path = _safe_key(_sanitize_path(_safe_key(body.get("path") or "")))
    return await intent.signed_upload_url(user.id, path)


@router.put("/upload-target")
async def upload_target(request: Request, file_id: UUID, version_id: UUID,
                        user: User = Depends(require_user)) -> dict:
    return await intent.accept_upload(user.id, file_id, version_id, await request.body(),
                                      request.headers.get("content-type"))


@router.post("/folder")
async def create_folder(body: dict = Body(...), user: User = Depends(require_user)) -> dict:
    path = _safe_key(_sanitize_path(_safe_key(body.get("path") or "")))
    return await intent.create_folder(user.id, path)


@router.post("/{file_id}/versions/{version_id}/complete")
async def complete_upload(file_id: UUID, version_id: UUID,
                          user: User = Depends(require_user)) -> dict:
    return await intent.complete_upload(user.id, file_id, version_id)


@router.post("/move")
async def move(body: dict = Body(...), user: User = Depends(require_user)) -> dict:
    source = _safe_key(body.get("from") or "")
    destination = _safe_key(_sanitize_path(_safe_key(body.get("to") or "")))
    return await intent.move(user.id, source, destination)


@router.delete("")
async def delete(path: str, kind: str = Query("file", pattern="^(file|folder)$"),
                 user: User = Depends(require_user)) -> dict:
    return await intent.delete(user.id, [_safe_key(path)])


@router.get("/lecture-materials")
async def lecture_materials(course_code: str, user: User = Depends(require_user)) -> dict:
    row = await db.fetchrow(
        "SELECT folder_name FROM courses WHERE user_id = %s AND code = %s LIMIT 1",
        user.id, course_code.upper(),
    )
    if row is None:
        raise HTTPException(400, "unknown course code")
    folder = (row.get("folder_name") or "").strip() or course_code.upper()
    grouped: dict[str, list[dict]] = {}
    for entry in await intent.list_files(user.id, folder, limit=1000):
        if entry["type"] != "file":
            continue
        match = re.match(r"^(\d{1,3})_lecture", entry["name"])
        group = str(int(match.group(1))) if match else ""
        grouped.setdefault(group, []).append({"name": entry["name"], "path": entry["path"]})
    return grouped


@router.get("/search")
async def search(q: str = Query(..., min_length=2), limit: int = Query(20, ge=1, le=100),
                 user: User = Depends(require_user)) -> list[dict]:
    return await intent.search(user.id, q, limit)


@router.get("/raw")
async def raw_file(request: Request, path: str | None = None, file_id: UUID | None = None,
                   version_id: UUID | None = None, user: User = Depends(require_user)):
    if file_id is not None and version_id is not None and path is None:
        meta = await intent.get_version(user.id, file_id, version_id)
    elif path is not None and file_id is None and version_id is None:
        meta = await intent.current_version(user.id, path)
    else:
        raise HTTPException(400, "provide a path or a file and version ID")
    data = await intent.read_version(user.id, meta["file_id"], meta["id"])
    name = meta["logical_path"].rsplit("/", 1)[-1]
    mime = meta["content_type"]
    disposition = "inline" if mime in _INLINE_MIMETYPES else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=utf-8''{quote(name, safe='')}",
        "Cache-Control": "private, max-age=3600" if version_id else "private, no-cache",
        "X-Content-Type-Options": "nosniff", "Accept-Ranges": "bytes",
        "ETag": f'"{meta["id"]}"',
    }
    status = 200
    requested = request.headers.get("range")
    if requested and request.headers.get("if-range", headers["ETag"]) == headers["ETag"]:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
        if not match or not any(match.groups()) or not data:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(data)}"})
        first, last = match.groups()
        start = int(first) if first else max(0, len(data) - int(last))
        end = min(int(last), len(data) - 1) if first and last else len(data) - 1
        if start > end or (not first and int(last) == 0):
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(data)}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        data, status = data[start:end + 1], 206
    return Response(data, media_type=mime, headers=headers, status_code=status)


@router.post("/sync-materials")
async def sync_materials(course: Optional[str] = Query(default=None)) -> dict[str, Any]:
    raise HTTPException(
        503,
        "Material sync must use versioned file uploads before it can resume on this deployment.",
    )
