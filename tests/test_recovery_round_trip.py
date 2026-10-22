"""Restore a real PostgreSQL export into another instance and read recovered data."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
import psycopg
import pymupdf
from testcontainers.postgres import PostgresContainer

from tests.test_backup_recovery import recovery


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("recovery_bootstrap", ROOT / "infra/aws/scripts/bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


async def test_real_export_restores_login_business_data_pdf_and_mcp(tmp_path, monkeypatch):
    config = {key: "test-only" for key in bootstrap.REQUIRED_KEYS}
    config.update(OPERATOR_EMAIL="operator@example.test", OPERATOR_PASSWORD="operator-password",
                  SMOKE_EMAIL="smoke@example.test", SMOKE_PASSWORD="smoke-password",
                  APP_DB_PASSWORD="runtime-password", MIGRATION_DB_PASSWORD="maintenance-password",
                  SMOKE_MCP_TOKEN="recovered-mcp-token", SESSION_SECRET="round-trip-session-secret",
                  SECRETS_ENCRYPTION_KEY=Fernet.generate_key().decode())
    monkeypatch.setenv("DB_NAME", "study")
    monkeypatch.setenv("PGSSLMODE", "disable")  # Disposable loopback-only test databases.
    with PostgresContainer("postgres:16-alpine", username="classport", password="testpw", dbname="study") as source, PostgresContainer("postgres:16-alpine", username="classport", password="testpw", dbname="study") as target:
        def dsn(pg, role="classport", password="testpw"):
            return f"postgresql://{role}:{password}@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/study"

        bootstrap.provision_roles(dsn(source), config)
        source_url = dsn(source, "classport_migrator", config["MIGRATION_DB_PASSWORD"])
        subprocess.run([sys.executable, str(ROOT / "scripts/run_migrations.py")],
                       env={**os.environ, "MIGRATION_DATABASE_URL": source_url},
                       capture_output=True, check=True)
        bootstrap.grant_runtime_access(source_url)
        bootstrap.seed_smoke(source_url, config)
        key = Fernet(config["SECRETS_ENCRYPTION_KEY"].encode())
        with psycopg.connect(source_url) as conn:
            conn.execute("UPDATE users SET email=%s,password_hash=%s WHERE id=%s", (
                config["OPERATOR_EMAIL"], PasswordHasher().hash(config["OPERATOR_PASSWORD"]), bootstrap.OPERATOR_ID))
            conn.execute("INSERT INTO courses(user_id,code,full_name) VALUES (%s,'DRILL','Recovery course')", (bootstrap.OPERATOR_ID,))
            conn.execute("INSERT INTO courses(user_id,code,full_name) VALUES (%s,'MCPDRILL','Recovered MCP course')", (bootstrap.SMOKE_ID,))
            conn.execute("INSERT INTO tasks(user_id,title,course_code) VALUES (%s,'Recover this task','DRILL')", (bootstrap.OPERATOR_ID,))
            conn.execute("INSERT INTO user_secrets(user_id,telegram_bot_token_enc) VALUES (%s,%s)", (
                bootstrap.OPERATOR_ID, key.encrypt(b"restored-private-value")))
        materials = tmp_path / "original"
        materials.mkdir()
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "Recovery drill PDF")
            original_bytes = pdf.tobytes()
        from app import db
        from app.services import file_index, file_records
        monkeypatch.setenv("STUDY_ROOT", str(materials))
        monkeypatch.setattr(db, "_pool", None)
        await db.init_pool(dsn(source, "classport_app", config["APP_DB_PASSWORD"]))
        try:
            await file_records.create_folder(bootstrap.OPERATOR_ID, "DRILL/empty")
            version = await file_records.upload(bootstrap.OPERATOR_ID, "DRILL/sample.pdf", original_bytes)
            assert (await file_index.index_file(
                bootstrap.OPERATOR_ID, version["file_id"], version["version_id"],
            ))["status"] == "indexed"
        finally:
            await db.close_pool()
        with psycopg.connect(source_url) as conn:
            original_state = conn.execute(recovery.STATE_SQL).fetchone()[0]
        assert original_state["file_versions"] == 1
        assert original_state["references"][0].startswith("objects/")
        files = recovery.check_files(materials, original_state)
        with tarfile.open(tmp_path / "courses.tar.gz", "w:gz") as archive:
            archive.add(materials, arcname=".")
        exported = source.get_wrapped_container().exec_run([
            "pg_dump", "-h", "127.0.0.1", "-U", "classport_migrator", "-d", "study", "-Fc"],
            environment={"PGPASSWORD": config["MIGRATION_DB_PASSWORD"]})
        assert exported.exit_code == 0, exported.output.decode(errors="replace")
        # Reproduce changes/loss after the backup; restoration must recover the old data.
        (materials / original_state["references"][0]).unlink()
        with psycopg.connect(source_url) as conn:
            conn.execute("UPDATE courses SET full_name='Changed after backup' WHERE code='DRILL'")
        bootstrap.provision_roles(dsn(target), config)
        restored = subprocess.run([
            "docker", "exec", "-i", "--env", "PGPASSWORD=" + config["MIGRATION_DB_PASSWORD"],
            target.get_wrapped_container().id, "pg_restore", "-h", "127.0.0.1", "-U", "classport_migrator",
            "-d", "study", "--clean", "--if-exists", "--no-owner", "--exit-on-error", "--single-transaction"],
            input=exported.output, capture_output=True)
        assert restored.returncode == 0, restored.stderr.decode()
        target_url = dsn(target, "classport_migrator", config["MIGRATION_DB_PASSWORD"])
        bootstrap.grant_runtime_access(target_url)
        with psycopg.connect(target_url) as conn:
            assert conn.execute(recovery.STATE_SQL).fetchone()[0] == original_state
            encrypted = conn.execute("SELECT telegram_bot_token_enc FROM user_secrets").fetchone()[0]
            assert key.decrypt(bytes(encrypted)) == b"restored-private-value"
        recovered = tmp_path / "recovered"
        recovery.extract(tmp_path / "courses.tar.gz", recovered)
        recovery.check_files(recovered, original_state, files)

        from app import db
        from app.config import get_settings
        monkeypatch.setenv("DATABASE_URL", dsn(target, "classport_app", config["APP_DB_PASSWORD"]))
        monkeypatch.setenv("SESSION_SECRET", config["SESSION_SECRET"])
        monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", config["SECRETS_ENCRYPTION_KEY"])
        monkeypatch.setenv("STUDY_ROOT", str(recovered))
        get_settings.cache_clear()
        from app.main import create_app
        monkeypatch.setattr(db, "_pool", None)
        await db.init_pool()
        try:
            async with AsyncClient(transport=ASGITransport(create_app()), base_url="https://test") as client:
                login = await client.post("/api/auth/login", json={"email": config["OPERATOR_EMAIL"], "password": config["OPERATOR_PASSWORD"]})
                assert login.status_code == 200 and login.json()["authed"] is True
                courses = await client.get("/api/courses")
                assert courses.json()[0]["full_name"] == "Recovery course"
                tasks = await client.get("/api/tasks")
                assert tasks.json()[0]["title"] == "Recover this task"
                pdf = await client.get("/api/files/raw", params={"path": "DRILL/sample.pdf"})
                assert pdf.status_code == 200 and pdf.content == original_bytes
                entries = await client.get("/api/files/list", params={"prefix": "DRILL"})
                assert entries.json()[0]["path"] == "DRILL/empty"
                hits = await client.get("/api/files/search", params={"q": "Recovery"})
                assert hits.json()[0]["path"] == "DRILL/sample.pdf"
                assert "inline" in pdf.headers["content-disposition"]
                with pymupdf.open(stream=pdf.content, filetype="pdf") as document:
                    assert document.page_count == 1
                    document[0].get_pixmap()
                mcp = await client.post("/mcp/", headers={"Authorization": "Bearer " + config["SMOKE_MCP_TOKEN"],
                                                        "Accept": "application/json, text/event-stream"},
                                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_courses", "arguments": {}}})
                assert mcp.status_code == 200
                payload = json.loads(mcp.text) if mcp.headers["content-type"].startswith("application/json") else next(
                    json.loads(line[6:]) for line in mcp.text.splitlines() if line.startswith("data: "))
                assert payload["result"]["isError"] is False
                assert "Recovered MCP course" in json.dumps(payload)
        finally:
            await db.close_pool()
            get_settings.cache_clear()
