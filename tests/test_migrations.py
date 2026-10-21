"""Verify committed state and mutual exclusion against disposable Postgres."""
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from scripts import run_migrations as runner


@pytest.fixture
def migration_db(pg_url, tmp_path, monkeypatch):
    name = "migration_test_" + uuid4().hex
    with psycopg.connect(pg_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        dsn = pg_url.rsplit("/", 1)[0] + "/" + name
        monkeypatch.setenv("MIGRATION_DATABASE_URL", dsn)
        monkeypatch.setattr(runner, "MIGRATIONS_DIR", tmp_path)
        try:
            yield dsn, tmp_path
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_failed_batch_rolls_back_and_successful_invocation_stays_committed(migration_db):
    dsn, directory = migration_db
    (directory / "001.sql").write_text("CREATE TABLE first_file (id int);")
    bad = directory / "002.sql"
    bad.write_text("SELECT * FROM intentionally_missing_table;")
    assert runner.main() == 1
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SELECT to_regclass('public.first_file')").fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM public._migrations").fetchone()[0] == 0
    bad.unlink()
    assert runner.main() == 0
    assert runner.main() == 0
    bad.write_text("SELECT * FROM intentionally_missing_table;")
    assert runner.main() == 1
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SELECT to_regclass('public.first_file')").fetchone()[0] == "first_file"
        assert conn.execute("SELECT count(*) FROM public._migrations").fetchone()[0] == 1


def test_second_runner_cannot_start_while_database_lock_is_held(migration_db):
    dsn, directory = migration_db
    (directory / "001.sql").write_text("CREATE TABLE only_once (id int);")
    with psycopg.connect(dsn, autocommit=True) as first:
        first.execute("SELECT pg_advisory_lock(%s)", (runner.MIGRATION_LOCK_ID,))
        assert runner.main() == 1
        assert first.execute("SELECT to_regclass('public._migrations')").fetchone()[0] is None
    assert runner.main() == 0
