"""Exercise AWS's first-install database setup without AWS services or credentials."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote
from unittest.mock import Mock
import json

from argon2 import PasswordHasher
from cryptography.fernet import Fernet
import psycopg
from psycopg import sql
import pytest
from testcontainers.postgres import PostgresContainer


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("aws_bootstrap", ROOT / "infra/aws/scripts/bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_config_validation_and_raw_secret_files(tmp_path):
    config = {key: "test-value" for key in bootstrap.REQUIRED_KEYS}
    config.update(
        OPERATOR_EMAIL="operator@example.test", SMOKE_EMAIL="smoke@example.test",
        APP_DB_PASSWORD="app-only", MIGRATION_DB_PASSWORD="maintenance-only",
        SECRETS_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    bootstrap.validate_config(config)
    with pytest.raises(ValueError):
        bootstrap.validate_config({**config, "SESSION_SECRET": "value\nMIGRATION_DATABASE_URL=bad"})
    with pytest.raises(ValueError):
        bootstrap.validate_config({**config, "SMOKE_EMAIL": config["OPERATOR_EMAIL"]})
    target = tmp_path / ".env"
    target.touch(mode=0o644)
    bootstrap.write_env(target, {"APP_PASSWORD_HASH": "$argon2id$literal-value"})
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.read_text() == "APP_PASSWORD_HASH=$argon2id$literal-value\n"


@pytest.mark.parametrize("fail_migration", [False, True])
def test_database_preparation_runs_one_migration_before_grants_and_accounts(monkeypatch, tmp_path, fail_migration):
    for name in ("PGSSLMODE", "PGSSLROOTCERT", "PGCONNECT_TIMEOUT"):
        monkeypatch.setenv(name, "")
    config = {key: "test-secret" for key in bootstrap.REQUIRED_KEYS}
    config.update(OPERATOR_EMAIL="operator@example.test", SMOKE_EMAIL="smoke@example.test",
                  APP_DB_PASSWORD="runtime-only", MIGRATION_DB_PASSWORD="maintenance-only",
                  SECRETS_ENCRYPTION_KEY=Fernet.generate_key().decode())
    monkeypatch.setenv("DB_HOST", "database.example.test")
    monkeypatch.setenv("DB_NAME", "study")
    monkeypatch.setenv("PUBLIC_URL", "https://study.example.test")
    calls = []
    monkeypatch.setattr(bootstrap, "provision_roles", lambda *args: calls.append("roles"))
    monkeypatch.setattr(bootstrap, "grant_runtime_access", lambda *args: calls.append("grants"))
    monkeypatch.setattr(bootstrap, "seed_smoke", lambda *args: calls.append("smoke_account"))

    def run(args, **kwargs):
        calls.append(Path(args[-1]).name)
        if fail_migration:
            raise subprocess.CalledProcessError(1, args, stderr="test-secret")

    runner = Mock(side_effect=run)
    monkeypatch.setattr(bootstrap.subprocess, "run", runner)
    result_file = tmp_path / "database-result.json"
    if fail_migration:
        with pytest.raises(subprocess.CalledProcessError):
            bootstrap.bootstrap(config, {"username": "admin", "password": "test-secret"}, tmp_path, result_file)
        assert calls == ["roles", "run_migrations.py"]
    else:
        bootstrap.bootstrap(config, {"username": "admin", "password": "test-secret"}, tmp_path, result_file)
        assert calls == ["roles", "run_migrations.py", "grants", "seed_operator_password.py", "smoke_account"]
        assert not (tmp_path / ".env.migration").exists()
        assert "MIGRATION_DATABASE_URL" not in (tmp_path / ".env").read_text()
    report = json.loads(result_file.read_text())
    assert report["steps"]["migration"] == ("failed" if fail_migration else "succeeded")
    assert report["status"] == ("failed" if fail_migration else "succeeded")
    assert "test-secret" not in result_file.read_text()


def test_fresh_database_uses_separate_non_admin_runtime_role(monkeypatch):
    with PostgresContainer("postgres:16-alpine", username="root_admin", password="testpw", dbname="aws_test") as pg:
        root_dsn = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        host, port = pg.get_container_host_ip(), pg.get_exposed_port(5432)

        def dsn(user, password):
            return f"postgresql://{user}:{quote(password, safe='')}@{host}:{port}/aws_test"

        # Model RDS's administrator: can create users, but is not a superuser
        # and cannot grant BYPASSRLS to an application role.
        with psycopg.connect(root_dsn) as conn:
            conn.execute("CREATE ROLE rds_admin LOGIN CREATEROLE CREATEDB PASSWORD 'admin-test'")
            conn.execute("ALTER DATABASE aws_test OWNER TO rds_admin")
        config = {
            "APP_DB_PASSWORD": "runtime-@-$-password", "MIGRATION_DB_PASSWORD": "maintenance-password",
            "SMOKE_EMAIL": "smoke@example.test", "SMOKE_PASSWORD": "smoke-test-password",
            "SMOKE_MCP_TOKEN": "smoke-test-token",
        }
        monkeypatch.setenv("DB_NAME", "aws_test")
        admin_dsn = dsn("rds_admin", "admin-test")
        migration_dsn = dsn("classport_migrator", config["MIGRATION_DB_PASSWORD"])
        for _ in range(2):
            bootstrap.provision_roles(admin_dsn, config)
            env = {**os.environ, "MIGRATION_DATABASE_URL": migration_dsn}
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_migrations.py")],
                env=env, capture_output=True, text=True, timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            bootstrap.grant_runtime_access(migration_dsn)
            bootstrap.seed_smoke(migration_dsn, config)
        with psycopg.connect(dsn("classport_app", config["APP_DB_PASSWORD"])) as conn:
            flags = conn.execute(
                "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
            assert flags == (False, False, False, False)
            user = conn.execute("SELECT id, password_hash FROM users WHERE email = %s", (config["SMOKE_EMAIL"],)).fetchone()
            assert str(user[0]) == bootstrap.SMOKE_ID
            assert PasswordHasher().verify(user[1], config["SMOKE_PASSWORD"])
            assert conn.execute("SELECT count(*) FROM oauth_tokens WHERE token = %s AND expires_at > now()", (config["SMOKE_MCP_TOKEN"],)).fetchone()[0] == 1
            conn.execute("INSERT INTO courses (code, full_name, user_id) VALUES ('AWS', 'AWS bootstrap test', %s)", (bootstrap.SMOKE_ID,))
            assert conn.execute("SELECT count(*) FROM courses WHERE code = 'AWS'").fetchone()[0] == 1
            for statement in (
                sql.SQL("SELECT * FROM public._migrations"),
                sql.SQL("CREATE TABLE public.unwanted_table (id int)"),
                sql.SQL("SET ROLE classport_migrator"),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    conn.execute(statement)
