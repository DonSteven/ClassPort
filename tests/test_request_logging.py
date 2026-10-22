import asyncio
import json
import logging

from httpx import ASGITransport, AsyncClient
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.logging_config import JsonFormatter, RequestLoggingMiddleware, operation, request_id


@pytest.fixture
def records():
    rows = []

    class Capture(logging.Handler):
        def emit(self, record):
            rows.append(json.loads(JsonFormatter().format(record)))

    logger = logging.getLogger("app.operations")
    handler = Capture()
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    yield rows
    logger.removeHandler(handler)
    logger.setLevel(previous)


async def test_concurrent_requests_correlate_operations_without_logging_secrets(records):
    async def endpoint(request):
        with operation("storage.write"):
            await asyncio.sleep(0.01)
        return JSONResponse({"request_id": request_id.get()})

    app = RequestLoggingMiddleware(Starlette(routes=[Route("/files", endpoint)]))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        responses = await asyncio.gather(*(client.get("/files?token=oauth-secret", headers={
            "Authorization": "Bearer bearer-secret", "Cookie": "session=cookie-secret",
            "X-Request-ID": "untrusted-id"}) for _ in range(4)))
    ids = {response.headers["x-request-id"] for response in responses}
    assert len(ids) == 4
    for response in responses:
        assert response.json()["request_id"] == response.headers["x-request-id"]
    for identifier in ids:
        related = [row for row in records if row["request_id"] == identifier]
        assert {row["event"] for row in related} == {"request.completed", "operation.completed"}
        assert all(row["duration_ms"] >= 0 for row in related)
    assert request_id.get() is None
    assert not any(secret in json.dumps(records) for secret in (
        "oauth-secret", "bearer-secret", "cookie-secret", "untrusted-id"))


async def test_unhandled_failure_has_safe_trace_and_response_id(records):
    async def failed(request):
        with operation("database.query"):
            raise ConnectionError("postgresql://admin:database-secret@host/db")

    app = RequestLoggingMiddleware(Starlette(routes=[Route("/broken", failed)]))
    async with AsyncClient(transport=ASGITransport(app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/broken")
    assert response.status_code == 500 and response.headers["x-request-id"]
    assert records[-1]["status"] == 500
    errors = [row for row in records if row["event"] == "operation.failed"]
    assert errors[0]["stage"] == "database.query"
    assert errors[0]["error_type"] == "ConnectionError" and errors[0]["trace"]
    assert "database-secret" not in json.dumps(records) + response.text


def test_legacy_arguments_and_exception_messages_are_never_formatted():
    record = logging.LogRecord("httpx", logging.ERROR, __file__, 1,
                               "Request failed: %s", ("https://host/bot-credential?token=secret",), None)
    assert "credential" not in JsonFormatter().format(record)
    assert "token=secret" not in JsonFormatter().format(record)
