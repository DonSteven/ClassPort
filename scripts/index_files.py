"""Operator command to index completed database file versions.

Run with DATABASE_URL and STUDY_ROOT in the environment:
    uv run --no-sync python scripts/index_files.py

Unchanged immutable versions are skipped. Legacy files must be imported first.
"""
import asyncio
import logging
import sys
from pathlib import Path

# Allow running from anywhere by putting the repo root on sys.path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.logging_config import configure_logging  # noqa: E402

configure_logging()

from app import db  # noqa: E402
from app.services.file_index import index_all  # noqa: E402


async def _main() -> dict:
    await db.init_pool()
    try:
        return await index_all()
    finally:
        await db.close_pool()


if __name__ == "__main__":
    stats = asyncio.run(_main())
    logging.getLogger(__name__).info("index.completed", extra={"event": "index.completed", **stats})
