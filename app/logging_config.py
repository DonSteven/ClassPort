"""Safe JSON logs shared by HTTP requests and standalone maintenance jobs."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from time import perf_counter
import traceback
from uuid import uuid4


request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
job_context: ContextVar[dict] = ContextVar("job_context", default={})
FIELDS = {"event", "stage", "method", "path", "status", "duration_ms", "error_type", "indexed", "skipped", "failed", "pruned", "total_seen", "user_id", "file_id", "version_id", "error_code", "job_id", "attempt", "execution_id", "worker_id", "job_type", "pipeline_version", "queue_wait_ms", "processing_ms", "end_to_end_ms", "file_size", "storage_backend"}
log = logging.getLogger("app.operations")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Never format legacy arguments: URLs, SQL parameters and exception
        # messages can contain credentials. Trace frames omit source and locals.
        data = {"timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname, "logger": record.name,
                "request_id": request_id.get(), "event": "log.message"}
        data.update({key: record.__dict__[key] for key in FIELDS if key in record.__dict__})
        data.update(job_context.get())
        if record.exc_info and record.exc_info[0]:
            data["error_type"] = record.exc_info[0].__name__
            data["trace"] = [{"file": Path(frame.filename).name, "line": frame.lineno,
                              "function": frame.name}
                             for frame in traceback.extract_tb(record.exc_info[2])]
        return json.dumps(data, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


@contextmanager
def operation(stage, **fields):
    started = perf_counter()
    try:
        yield
    except Exception:
        log.exception("operation.failed", extra={**fields, "event": "operation.failed", "stage": stage,
                      "duration_ms": round((perf_counter() - started) * 1000, 2)})
        raise
    else:
        log.info("operation.completed", extra={**fields, "event": "operation.completed", "stage": stage,
                 "duration_ms": round((perf_counter() - started) * 1000, 2)})


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from .auth import set_current_user_id

        token = request_id.set(uuid4().hex)
        set_current_user_id(None)
        started = perf_counter()
        status = 500
        response_started = False
        response_finished = None

        async def logged_send(message):
            nonlocal status, response_started, response_finished
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                message = {**message, "headers": [*message.get("headers", []),
                           (b"x-request-id", request_id.get().encode())]}
            elif message["type"] == "http.response.body" and not message.get("more_body"):
                response_finished = perf_counter()
            await send(message)

        try:
            await self.app(scope, receive, logged_send)
        except Exception:
            log.exception("request.failed", extra={"event": "request.failed", "stage": "request"})
            if not response_started:
                from starlette.responses import JSONResponse
                await JSONResponse({"detail": "Internal server error"}, status_code=500)(
                    scope, receive, logged_send)
            raise
        finally:
            route = scope.get("route")
            log.info("request.completed", extra={"event": "request.completed",
                     "method": scope["method"], "path": getattr(route, "path", "unmatched"),
                     "status": status,
                     "duration_ms": round(((response_finished or perf_counter()) - started) * 1000, 2)})
            set_current_user_id(None)
            request_id.reset(token)
