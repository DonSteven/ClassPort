"""Each prefork child owns its event loop and PostgreSQL connection pool."""
import asyncio
import os
import socket
from uuid import UUID

from celery.signals import setup_logging, worker_process_init, worker_process_shutdown

from . import db
from .celery_app import celery
from .logging_config import configure_logging
from .services import jobs

_runner: asyncio.Runner | None = None


@setup_logging.connect
def logging_setup(**kwargs):
    configure_logging()


@worker_process_init.connect
def child_start(**kwargs):
    global _runner
    # No database connection is opened in the parent or in this startup hook.
    _runner = asyncio.Runner()


@worker_process_shutdown.connect
def child_stop(**kwargs):
    global _runner
    if _runner is not None:
        _runner.run(db.close_pool())
        _runner.close()
        _runner = None


async def _execute(job_id: UUID):
    await db.init_pool()
    await jobs.run(job_id, f"{socket.gethostname()}:{os.getpid()}")


@celery.task(name="classport.process_job")
def process_job(job_id: str):
    if _runner is None:
        raise RuntimeError("Start a prefork Celery worker to execute file jobs")
    _runner.run(_execute(UUID(job_id)))
