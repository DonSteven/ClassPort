"""Authenticated views of durable processing state, never Celery result state."""
from uuid import UUID

from fastapi import APIRouter, Depends

from ..auth import User, require_user
from ..services import jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(file_id: UUID | None = None, user: User = Depends(require_user)) -> list[dict]:
    return await jobs.list_jobs(user.id, file_id)


@router.get("/{job_id}")
async def get_job(job_id: UUID, user: User = Depends(require_user)) -> dict:
    return await jobs.get(user.id, job_id)


@router.post("/{job_id}/retry")
async def retry_job(job_id: UUID, user: User = Depends(require_user)) -> dict:
    return await jobs.retry(user.id, job_id)
