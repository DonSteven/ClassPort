"""Message transport only; results and retry scheduling live in PostgreSQL."""
import os

from celery import Celery

from .job_config import TIME_LIMIT

celery = Celery("classport", broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
                include=["app.worker"])
celery.conf.update(
    accept_content=["json"], task_serializer="json", task_ignore_result=True,
    task_default_queue="file-processing", task_acks_late=True,
    task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    task_time_limit=TIME_LIMIT, broker_connection_timeout=2,
    broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2,
                              "visibility_timeout": TIME_LIMIT + 60},
    worker_hijack_root_logger=False,
)


def publish(job_id: str) -> None:
    celery.send_task("classport.process_job", args=[job_id], retry=False)
