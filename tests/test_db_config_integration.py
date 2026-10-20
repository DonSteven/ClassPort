"""Exercise separate database identities and standalone operator initialization."""
from pathlib import Path
import ipaddress
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest


@pytest.mark.asyncio
async def test_runtime_pool_uses_dml_role_without_migration_credentials(pg_url, monkeypatch):
    from app import db
    from app.db_config import get_migration_dsn

    suffix = uuid4().hex[:12]
    migration_role = f"migration_{suffix}"
    runtime_role = f"runtime_{suffix}"
    schema = f"db_config_{suffix}"
    password = "disposable-test-password"
    target = urlsplit(pg_url)
    address = target.netloc.rsplit("@", 1)[-1]
    migration_dsn = f"postgresql://{migration_role}:{password}@{address}{target.path}"
    runtime_dsn = f"postgresql://{runtime_role}:{password}@{address}{target.path}"
    with psycopg.connect(pg_url, autocommit=True) as admin:
        for role in (migration_role, runtime_role):
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password),
            ))
        try:
            admin.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                sql.Identifier(schema), sql.Identifier(migration_role),
            ))
            monkeypatch.setenv("MIGRATION_DATABASE_URL", migration_dsn)
            with psycopg.connect(get_migration_dsn()) as migration:
                assert migration.execute("SELECT current_user").fetchone()[0] == migration_role
                migration.execute(sql.SQL("CREATE TABLE {}.items (name text)").format(
                    sql.Identifier(schema),
                ))
                migration.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(runtime_role),
                ))
                migration.execute(sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON {}.items TO {}"
                ).format(sql.Identifier(schema), sql.Identifier(runtime_role)))

            monkeypatch.delenv("MIGRATION_DATABASE_URL")
            monkeypatch.setenv("DATABASE_URL", runtime_dsn)
            monkeypatch.setenv("DB_POOL_MIN_SIZE", "1")
            monkeypatch.setenv("DB_POOL_MAX_SIZE", "1")
            monkeypatch.setenv("DB_POOL_TIMEOUT", "5")
            monkeypatch.setattr(db, "_pool", None)
            await db.init_pool()
            try:
                async with db.pool().connection() as conn:
                    row = await (await conn.execute("SELECT current_user AS name")).fetchone()
                    assert row["name"] == runtime_role
                    await conn.execute(sql.SQL("INSERT INTO {}.items VALUES ('course')").format(
                        sql.Identifier(schema),
                    ))
                    row = await (await conn.execute(sql.SQL("SELECT name FROM {}.items").format(
                        sql.Identifier(schema),
                    ))).fetchone()
                    assert row["name"] == "course"
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    async with db.pool().connection() as conn:
                        await conn.execute(sql.SQL("CREATE TABLE {}.forbidden (id int)").format(
                            sql.Identifier(schema),
                        ))
            finally:
                await db.close_pool()
        finally:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            for role in (runtime_role, migration_role):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_operator_seed_uses_migration_identity_without_web_settings(pg_url, tmp_path):
    operator_id = str(uuid4())
    script = Path(__file__).resolve().parents[1] / "scripts" / "seed_operator_password.py"
    env = {
        "MIGRATION_DATABASE_URL": pg_url,
        "DATABASE_URL": "postgresql://wrong:wrong@127.0.0.1:1/wrong",
        "OPERATOR_USER_ID": operator_id,
        "OPERATOR_EMAIL": f"operator-{operator_id}@test",
        "OPERATOR_DISPLAY_NAME": "First name",
        "APP_PASSWORD_HASH": "initial-hash",
    }
    with psycopg.connect(pg_url, autocommit=True) as admin:
        try:
            for display, password in (("First name", "initial-hash"), ("New name", "replacement")):
                env.update(OPERATOR_DISPLAY_NAME=display, APP_PASSWORD_HASH=password)
                result = subprocess.run(
                    [sys.executable, str(script)], cwd=tmp_path, env=env,
                    capture_output=True, text=True, timeout=15,
                )
                assert result.returncode == 0, result.stderr
            row = admin.execute(
                "SELECT display_name, password_hash FROM users WHERE id = %s", (operator_id,),
            ).fetchone()
            assert row == ("New name", "initial-hash")
        finally:
            admin.execute("DELETE FROM users WHERE id = %s", (operator_id,))


@pytest.mark.skipif(shutil.which("openssl") is None, reason="OpenSSL CLI is not installed")
def test_tls_verifies_server_certificate_and_hostname(pg_url, tmp_path, monkeypatch):
    from app.db_config import get_migration_dsn, get_runtime_dsn

    host = conninfo_to_dict(pg_url)["host"]
    try:
        ipaddress.ip_address(host)
        alternative_name = f"IP:{host}"
    except ValueError:
        alternative_name = f"DNS:{host}"
    certificate = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", f"/CN={host}", "-addext", f"subjectAltName={alternative_name}",
         "-keyout", str(key), "-out", str(certificate)],
        check=True, capture_output=True, timeout=15,
    )
    suffix = uuid4().hex
    paths = {"ssl_cert_file": (certificate, f"/tmp/cert_{suffix}.pem"),
             "ssl_key_file": (key, f"/tmp/key_{suffix}.pem")}
    with psycopg.connect(pg_url, autocommit=True) as admin:
        # Write disposable TLS material inside the testcontainer as postgres.
        admin.execute("CREATE TEMP TABLE tls_lines (line text)")
        for setting, (source, destination) in paths.items():
            admin.execute("TRUNCATE tls_lines")
            with admin.cursor().copy("COPY tls_lines FROM STDIN") as copy:
                for line in source.read_text().splitlines():
                    copy.write_row((line,))
            admin.execute(sql.SQL("COPY tls_lines TO {}").format(sql.Literal(destination)))
            admin.execute(sql.SQL("ALTER SYSTEM SET {} = {}").format(
                sql.Identifier(setting), sql.Literal(destination),
            ))
        # COPY uses the server's umask; PostgreSQL requires a private key mode
        # of 0600. This command runs only inside the disposable testcontainer.
        admin.execute(sql.SQL("COPY (SELECT NULL WHERE false) TO PROGRAM {}").format(
            sql.Literal(f"chmod 600 {paths['ssl_key_file'][1]}"),
        ))
        try:
            admin.execute("ALTER SYSTEM SET ssl = 'on'")
            admin.execute("SELECT pg_reload_conf()")
            deadline = time.monotonic() + 3
            while admin.execute("SHOW ssl").fetchone()[0] != "on":
                assert time.monotonic() < deadline, "Test PostgreSQL did not enable TLS"
                time.sleep(0.01)
            monkeypatch.setenv("DATABASE_URL", pg_url)
            monkeypatch.setenv("MIGRATION_DATABASE_URL", pg_url)
            monkeypatch.setenv("PGSSLMODE", "verify-full")
            monkeypatch.setenv("PGSSLROOTCERT", str(certificate))
            for getter in (get_runtime_dsn, get_migration_dsn):
                with psycopg.connect(getter()) as secure:
                    row = secure.execute(
                        "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
                    ).fetchone()
                    assert row == (True,)
            wrong_host = make_conninfo(
                get_runtime_dsn(), host="wrong.invalid", hostaddr=socket.gethostbyname(host),
            )
            with pytest.raises(psycopg.OperationalError, match="does not match host name"):
                psycopg.connect(wrong_host)
        finally:
            for setting in ("ssl", "ssl_cert_file", "ssl_key_file"):
                admin.execute(sql.SQL("ALTER SYSTEM RESET {}").format(sql.Identifier(setting)))
            admin.execute("SELECT pg_reload_conf()")
