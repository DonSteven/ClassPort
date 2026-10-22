"""Health endpoints remain usable without a database or authentication."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import db
from app.config import get_settings
from app.storage import local as storage
from app.services import events as events_svc


@pytest_asyncio.fixture
async def health_client(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "health-test-session-secret")
    get_settings.cache_clear()
    from app.main import create_app

    transport = ASGITransport(app=create_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        get_settings.cache_clear()


async def test_live_does_not_access_dependencies(health_client, monkeypatch):
    query = AsyncMock(side_effect=AssertionError("liveness must not query the database"))
    probe = AsyncMock(side_effect=AssertionError("liveness must not access storage"))
    monkeypatch.setattr(db, "fetchval", query)
    monkeypatch.setattr(storage.LocalStorage, "check_readable", probe)

    response = await health_client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    query.assert_not_awaited()
    probe.assert_not_awaited()


@pytest.mark.parametrize("path", ["/api/health", "/api/health/ready"])
async def test_ready_checks_dependencies_without_writing_events(health_client, monkeypatch, path):
    query = AsyncMock(return_value=1)
    record = AsyncMock()
    monkeypatch.setattr(db, "fetchval", query)
    monkeypatch.setattr(events_svc, "record_event", record)

    response = await health_client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["db"] == body["storage"] == "ok"
    query.assert_awaited_once_with("SELECT 1")
    record.assert_not_awaited()


@pytest.mark.parametrize("path", ["/api/health", "/api/health/ready"])
async def test_ready_recovers_after_database_failure(health_client, monkeypatch, path):
    query = AsyncMock(side_effect=[ConnectionError("database unavailable"), 1])
    monkeypatch.setattr(db, "fetchval", query)

    failed = await health_client.get(path)
    live = await health_client.get("/api/health/live")
    recovered = await health_client.get(path)

    assert failed.status_code == 503
    assert failed.json()["ok"] is False
    assert failed.json()["db"] == "error: database unavailable"
    assert live.status_code == 200
    assert recovered.status_code == 200
    assert recovered.json()["ok"] is True


async def test_ready_rejects_uninitialised_pool(health_client, monkeypatch):
    monkeypatch.setattr(db, "_pool", None)

    response = await health_client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert "not initialised" in response.json()["db"]


@pytest.mark.parametrize("kind", ["missing", "file", "unreadable"])
async def test_ready_rejects_unavailable_storage(health_client, tmp_path, monkeypatch, kind):
    monkeypatch.setattr(db, "fetchval", AsyncMock(return_value=1))
    root = tmp_path / "study-root"
    monkeypatch.setenv("STUDY_ROOT", str(root))
    if kind == "file":
        root.write_text("not a directory")
    elif kind == "unreadable":
        root.mkdir()
        monkeypatch.setattr(storage.os, "scandir", Mock(side_effect=PermissionError("denied")))

    response = await health_client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["db"] == "ok"
    assert response.json()["storage"].startswith("error:")


@pytest.mark.parametrize("dependency", ["db", "storage"])
async def test_ready_times_out_and_cancels_probe(health_client, monkeypatch, dependency):
    import app.main as main

    cancelled = asyncio.Event()

    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "READINESS_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(db, "fetchval", AsyncMock(return_value=1))
    if dependency == "db":
        monkeypatch.setattr(db, "fetchval", blocked)
    else:
        monkeypatch.setattr(storage.LocalStorage, "check_readable", blocked)

    async with asyncio.timeout(1):
        response = await health_client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()[dependency] == "error: readiness check timed out"
    assert cancelled.is_set()
