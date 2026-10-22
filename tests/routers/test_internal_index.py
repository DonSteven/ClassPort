"""Maintenance indexing requires an explicit user; all-user rebuilds are CLI-only."""
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.services import file_index
from scripts.index_files import parse_args


USER = UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.parametrize("route", ["index-files", "sync"])
async def test_internal_rebuild_requires_and_forwards_user(client, monkeypatch, route):
    monkeypatch.setenv("INTERNAL_API_SECRET", "test-secret")
    rebuild = AsyncMock(return_value={"indexed": 1, "failed": 0})
    monkeypatch.setattr(file_index, "index_user", rebuild)
    headers = {"X-Internal-Secret": "test-secret"}
    assert (await client.post(f"/api/internal/{route}", headers=headers)).status_code == 422
    rebuild.assert_not_awaited()
    response = await client.post(f"/api/internal/{route}", params={"user_id": str(USER)}, headers=headers)
    assert response.status_code == 200
    rebuild.assert_awaited_once_with(USER)


async def test_internal_rebuild_still_requires_secret(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "test-secret")
    response = await client.post("/api/internal/index-files", params={"user_id": str(USER)})
    assert response.status_code == 401


def test_operator_command_requires_explicit_scope():
    with pytest.raises(SystemExit) as caught:
        parse_args([])
    assert caught.value.code == 2
    assert parse_args(["--user-id", str(USER)]).user_id == USER
    assert parse_args(["--all-users"]).all_users is True
    with pytest.raises(SystemExit):
        parse_args(["--user-id", str(USER), "--all-users"])
