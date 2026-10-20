"""Database configuration selection without opening database connections."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.db_config import get_migration_dsn, get_pool_config, get_runtime_dsn


@pytest.fixture(autouse=True)
def database_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("POSTGRES_", "DB_POOL_")) or key in {
            "DATABASE_URL", "MIGRATION_DATABASE_URL", "PGHOST", "PGPORT",
            "PGSSLMODE", "PGSSLROOTCERT", "PGCONNECT_TIMEOUT",
        }:
            monkeypatch.delenv(key)


def test_urls_select_separate_identities(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:runtime@db/study")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://migrator:maintenance@db/study")
    assert conninfo_to_dict(get_runtime_dsn())["user"] == "app"
    assert conninfo_to_dict(get_migration_dsn())["user"] == "migrator"


@pytest.mark.parametrize("getter,variable", [
    (get_runtime_dsn, "DATABASE_URL"), (get_migration_dsn, "MIGRATION_DATABASE_URL"),
])
def test_url_encoded_credentials_are_parsed(getter, variable, monkeypatch):
    password = "with @:/ ?%' and \\ characters"
    monkeypatch.setenv(variable, (
        f"postgresql://local%20user:{quote(password, safe='')}@localhost:15432/local%20db"
    ))
    params = conninfo_to_dict(getter())
    assert params == {
        "user": "local user", "password": password, "dbname": "local db",
        "host": "localhost", "port": "15432", "connect_timeout": "5",
    }


@pytest.mark.parametrize("getter,required,other", [
    (get_runtime_dsn, "DATABASE_URL", "MIGRATION_DATABASE_URL"),
    (get_migration_dsn, "MIGRATION_DATABASE_URL", "DATABASE_URL"),
])
def test_missing_identity_never_uses_other_url(getter, required, other, monkeypatch):
    monkeypatch.setenv(other, "postgresql://wrong:secret@db/study")
    monkeypatch.setenv("POSTGRES_USER", "bootstrap")
    monkeypatch.setenv("POSTGRES_PASSWORD", "bootstrap-secret")
    monkeypatch.setenv("POSTGRES_DB", "study")
    monkeypatch.setenv(required, "")
    with pytest.raises(RuntimeError, match=f"{required} is required") as error:
        getter()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("getter,variable", [
    (get_runtime_dsn, "DATABASE_URL"), (get_migration_dsn, "MIGRATION_DATABASE_URL"),
])
def test_connection_setting_requires_a_postgres_url(getter, variable, monkeypatch):
    monkeypatch.setenv(variable, "host=db user=app password=secret dbname=study")
    with pytest.raises(ValueError, match=f"{variable} must be a PostgreSQL URL") as error:
        getter()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("getter,variable", [
    (get_runtime_dsn, "DATABASE_URL"), (get_migration_dsn, "MIGRATION_DATABASE_URL"),
])
def test_rds_tls_and_connect_timeout(getter, variable, monkeypatch):
    monkeypatch.setenv(variable, "postgresql://user:pw@instance.rds.amazonaws.com/study")
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    monkeypatch.setenv("PGSSLROOTCERT", "/certs/rds.pem")
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "8")
    params = conninfo_to_dict(getter())
    assert params["host"] == "instance.rds.amazonaws.com"
    assert params["sslmode"] == "verify-full"
    assert params["sslrootcert"] == "/certs/rds.pem"
    assert params["connect_timeout"] == "8"

    monkeypatch.setenv(variable, (
        "postgresql://user:pw@db/study?sslmode=disable&connect_timeout=2"
        "&sslrootcert=/explicit.pem"
    ))
    params = conninfo_to_dict(getter())
    assert params["sslmode"] == "disable"
    assert params["connect_timeout"] == "2"
    assert params["sslrootcert"] == "/explicit.pem"


def test_pool_defaults_and_overrides(monkeypatch):
    defaults = get_pool_config()
    assert (defaults.min_size, defaults.max_size, defaults.timeout) == (2, 10, 30)
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "0")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "4")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "1.5")
    config = get_pool_config()
    assert (config.min_size, config.max_size, config.timeout) == (0, 4, 1.5)


@pytest.mark.parametrize("variable,value", [
    ("DB_POOL_MIN_SIZE", "-1"), ("DB_POOL_MIN_SIZE", "11"),
    ("DB_POOL_MAX_SIZE", "0"), ("DB_POOL_MAX_SIZE", "1.5"),
    ("DB_POOL_TIMEOUT", "0"), ("DB_POOL_TIMEOUT", "-1"),
    ("DB_POOL_TIMEOUT", "nan"), ("DB_POOL_TIMEOUT", "inf"),
])
def test_invalid_pool_configuration_fails_clearly(variable, value, monkeypatch):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match="DB_POOL"):
        get_pool_config()


@pytest.mark.asyncio
async def test_init_pool_keeps_explicit_dsn_and_pool_settings(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setenv("DATABASE_URL", "invalid-deployment-url")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "3")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "2.5")
    instance = Mock(open=AsyncMock(), close=AsyncMock())
    constructor = Mock(return_value=instance)
    monkeypatch.setattr(db, "AsyncConnectionPool", constructor)
    dsn = "postgresql://tester:pw@localhost/test"
    await db.init_pool(dsn)
    await db.init_pool("should-not-replace-existing-pool")
    constructor.assert_called_once()
    assert constructor.call_args.args == (dsn,)
    options = constructor.call_args.kwargs
    assert (options["min_size"], options["max_size"], options["timeout"]) == (1, 3, 2.5)
    assert options["configure"] is db._configure_connection
    await db.close_pool()
    assert db._pool is None


@pytest.mark.parametrize("script", ["run_migrations.py", "seed_operator_password.py"])
def test_maintenance_imports_without_web_settings(script, tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts" / script
    result = subprocess.run(
        [sys.executable, "-c", (
            "import runpy, sys; "
            f"module = runpy.run_path({str(path)!r}); "
            "assert 'app.config' not in sys.modules; "
            "assert 'app.auth' not in sys.modules; "
            "assert 'app.main' not in sys.modules; "
            "module['get_migration_dsn']()"
        )],
        cwd=tmp_path,
        env={"MIGRATION_DATABASE_URL": "postgresql://migration:pw@db/study"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
