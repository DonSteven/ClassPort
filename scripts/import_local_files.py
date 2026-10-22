"""Import one user's legacy tree before enabling the database file browser.

Run with the application's runtime DATABASE_URL and STUDY_ROOT. Stop file
writers during import and verification; source files are never modified.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.services import file_records as files  # noqa: E402
from app.storage.local import LocalStorage  # noqa: E402


async def import_user(user_id: UUID, source: Path) -> dict:
    report = {"user_id": str(user_id), "files": 0, "directories": 0,
              "bytes": 0, "imported": 0, "reused": 0, "linked_indexes": 0, "errors": []}
    if not source.is_dir():
        report["errors"].append({"path": "", "error": "source directory is missing"})
        return report
    seen = set()
    for entry in sorted(source.rglob("*")):
        path = entry.relative_to(source).as_posix()
        if any(part.startswith(".") for part in entry.relative_to(source).parts):
            continue
        try:
            if entry.is_symlink():
                raise ValueError("symbolic links cannot be imported")
            if entry.is_dir():
                await files.create_folder(user_id, path)
                report["directories"] += 1
                continue
            data = await asyncio.to_thread(entry.read_bytes)
            sha = hashlib.sha256(data).hexdigest()
            report["files"] += 1
            report["bytes"] += len(data)
            seen.add(f"{user_id}/{path}")
            current = await files.stat(user_id, path)
            if current:
                version = await files.get_version(user_id, current["file_id"], current["version_id"])
                if version["sha256"] != sha or await files.download(user_id, path) != data:
                    raise ValueError("existing file conflicts with source content")
                report["reused"] += 1
            else:
                # Reuse the pending first version after a process interruption.
                pending = await db.fetchrow(
                    """SELECT f.id AS file_id, v.id AS version_id FROM files f JOIN file_versions v
                         ON v.user_id = f.user_id AND v.file_id = f.id
                       WHERE f.user_id = %s AND f.logical_path = %s AND f.deleted_at IS NULL
                         AND f.current_version_id IS NULL AND v.upload_status = 'pending'
                       ORDER BY v.revision DESC LIMIT 1""", user_id, path,
                )
                target = pending or await files.begin_upload(user_id, path)
                await files.accept_upload(user_id, target["file_id"], target["version_id"], data)
                await files.complete_upload(user_id, target["file_id"], target["version_id"])
                if await files.download(user_id, path) != data:
                    raise ValueError("copied content differs from source")
                report["imported"] += 1
                current = await files.stat(user_id, path)
            legacy = await db.fetchrow(
                "SELECT * FROM file_index WHERE user_id = %s AND path = %s",
                user_id, f"{user_id}/{path}",
            )
            if legacy:
                if legacy["sha256"] != sha or legacy["size"] != len(data):
                    raise ValueError("legacy index content differs; rebuild this index before cutover")
                if legacy["file_id"] and legacy["file_id"] != current["file_id"]:
                    raise ValueError("legacy index is already linked to another file")
                await db.execute(
                    """UPDATE file_index SET file_id = %s, file_version_id = %s, path = %s
                       WHERE user_id = %s AND path = %s""",
                    current["file_id"], current["version_id"], f"{user_id}/{current['file_id']}",
                    user_id, f"{user_id}/{path}",
                )
                report["linked_indexes"] += 1
        except (OSError, ValueError) as exc:
            report["errors"].append({"path": path, "error": str(exc)})
    unlinked = await db.fetch(
        "SELECT path FROM file_index WHERE user_id = %s AND file_id IS NULL", user_id,
    )
    for row in unlinked:
        if row["path"] not in seen:
            report["errors"].append({"path": row["path"], "error": "indexed source file is missing"})
    records = await db.fetch(
        """SELECT f.logical_path, v.size FROM files f JOIN file_versions v
             ON v.user_id = f.user_id AND v.file_id = f.id AND v.id = f.current_version_id
           WHERE f.user_id = %s AND f.deleted_at IS NULL AND v.upload_status = 'completed'""",
        user_id,
    )
    report["record_files"] = len(records)
    report["record_bytes"] = sum(row["size"] for row in records)
    for row in records:
        if f"{user_id}/{row['logical_path']}" not in seen:
            report["errors"].append({"path": row["logical_path"],
                                     "error": "registered source file is missing"})
    return report


async def main(args) -> dict:
    await db.init_pool()
    try:
        source = args.source_root or LocalStorage().root / str(args.user_id)
        return await import_user(args.user_id, source)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, type=UUID)
    parser.add_argument("--source-root", type=Path, help="Legacy directory for this user")
    result = asyncio.run(main(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result["errors"] else 0)
