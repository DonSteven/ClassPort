"""B1 file identity, version ownership and persistent job schema acceptance."""
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from scripts import run_migrations as runner


OPERATOR = "00000000-0000-0000-0000-000000000001"
MIGRATION = "20261022000001_file_jobs.sql"


async def make_file(conn, user_id=OPERATOR, path="lecture.pdf", kind="file"):
    return await (await conn.execute(
        "INSERT INTO files(user_id, logical_path, kind) VALUES (%s, %s, %s) RETURNING *",
        (user_id, path, kind),
    )).fetchone()


async def make_version(conn, file, revision=1):
    return await (await conn.execute(
        """INSERT INTO file_versions(user_id, file_id, revision, object_key)
           VALUES (%s, %s, %s, %s) RETURNING *""",
        (file["user_id"], file["id"], revision, f"{file['id']}/{revision}"),
    )).fetchone()


async def make_job(conn, version, pipeline="1"):
    return await (await conn.execute(
        """INSERT INTO jobs(user_id, file_id, file_version_id, job_type, pipeline_version)
           VALUES (%s, %s, %s, 'index_file', %s) RETURNING *""",
        (version["user_id"], version["file_id"], version["id"], pipeline),
    )).fetchone()


@pytest.mark.asyncio
async def test_current_version_and_indexed_version_are_independent(db_conn):
    async with db_conn.connection() as conn:
        file = await make_file(conn)
        v1 = await make_version(conn, file)
        v2 = await make_version(conn, file, 2)
        await conn.execute(
            "UPDATE files SET current_version_id = %s WHERE id = %s", (v2["id"], file["id"]),
        )
        await conn.execute(
            """INSERT INTO file_index(user_id, path, file_id, file_version_id, text_content)
               VALUES (%s, %s, %s, %s, 'old version')""",
            (OPERATOR, f"{OPERATOR}/lecture.pdf", file["id"], v1["id"]),
        )
        await conn.execute(
            "UPDATE files SET logical_path = 'renamed.pdf' WHERE id = %s", (file["id"],),
        )
        row = await (await conn.execute(
            """SELECT f.id, f.logical_path, f.current_version_id, i.file_version_id
               FROM files f JOIN file_index i ON i.user_id = f.user_id AND i.file_id = f.id
               WHERE f.id = %s""", (file["id"],),
        )).fetchone()
        assert row == {"id": file["id"], "logical_path": "renamed.pdf",
                       "current_version_id": v2["id"], "file_version_id": v1["id"]}


@pytest.mark.asyncio
async def test_directory_and_first_upload_allow_no_current_version(db_conn):
    async with db_conn.connection() as conn:
        directory = await make_file(conn, path="empty", kind="directory")
        file = await make_file(conn)
        pending = await make_version(conn, file)
        assert directory["current_version_id"] is None
        assert file["current_version_id"] is None
        assert pending["upload_status"] == "pending"
        assert pending["size"] is None


@pytest.mark.asyncio
async def test_active_path_unique_per_user_and_reusable_after_soft_delete(db_conn):
    async with db_conn.connection() as conn:
        file = await make_file(conn)
        with pytest.raises(psycopg.errors.UniqueViolation):
            async with conn.transaction():
                await make_file(conn, kind="directory")
        other_user = str(uuid4())
        await conn.execute("INSERT INTO users(id, email) VALUES (%s, %s)",
                           (other_user, f"{other_user}@test"))
        other = await make_file(conn, user_id=other_user)
        assert other["id"] != file["id"]
        await conn.execute("UPDATE files SET deleted_at = now() WHERE id = %s", (file["id"],))
        replacement = await make_file(conn)
        assert replacement["id"] != file["id"]


@pytest.mark.asyncio
async def test_duplicate_job_rejected_but_new_pipeline_allowed(db_conn):
    async with db_conn.connection() as conn:
        version = await make_version(conn, await make_file(conn))
        first = await make_job(conn, version)
        with pytest.raises(psycopg.errors.UniqueViolation):
            async with conn.transaction():
                await make_job(conn, version)
        updated_pipeline = await make_job(conn, version, pipeline="2")
        assert first["id"] != updated_pipeline["id"]


@pytest.mark.asyncio
async def test_attempts_retain_separate_execution_errors(db_conn):
    async with db_conn.connection() as conn:
        version = await make_version(conn, await make_file(conn))
        job = await make_job(conn, version)
        for number, error in [(1, "storage unavailable"), (2, "database unavailable")]:
            await conn.execute(
                """INSERT INTO job_attempts(user_id, job_id, attempt_no, execution_id,
                       worker_id, status, finished_at, error_message)
                   VALUES (%s, %s, %s, %s, 'worker-1', 'failed', now(), %s)""",
                (OPERATOR, job["id"], number, uuid4(), error),
            )
        attempts = await (await conn.execute(
            "SELECT error_message FROM job_attempts WHERE job_id = %s ORDER BY attempt_no",
            (job["id"],),
        )).fetchall()
        assert [a["error_message"] for a in attempts] == [
            "storage unavailable", "database unavailable",
        ]
        assert (await (await conn.execute(
            "SELECT count(*) AS n FROM jobs WHERE file_version_id = %s", (version["id"],),
        )).fetchone())["n"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("relationship", ["version_file", "job_version", "current_version",
                                          "index_version", "attempt_job", "outbox_job"])
async def test_cross_user_relationships_are_rejected(db_conn, relationship):
    async with db_conn.connection() as conn:
        user = str(uuid4())
        await conn.execute("INSERT INTO users(id, email) VALUES (%s, %s)", (user, f"{user}@test"))
        file = await make_file(conn)
        version = await make_version(conn, file)
        job = await make_job(conn, version)
        other_file = await make_file(conn, user_id=user)
        statements = {
            "version_file": (
                """INSERT INTO file_versions(user_id, file_id, revision, object_key)
                   VALUES (%s, %s, 2, 'wrong')""", (user, file["id"])),
            "job_version": (
                """INSERT INTO jobs(user_id, file_id, file_version_id, job_type, pipeline_version)
                   VALUES (%s, %s, %s, 'index_file', 'other')""",
                (user, other_file["id"], version["id"])),
            "current_version": (
                "UPDATE files SET current_version_id = %s WHERE id = %s",
                (version["id"], other_file["id"])),
            "index_version": (
                """INSERT INTO file_index(user_id, path, file_id, file_version_id)
                   VALUES (%s, 'wrong.pdf', %s, %s)""",
                (user, other_file["id"], version["id"])),
            "attempt_job": (
                """INSERT INTO job_attempts(user_id, job_id, attempt_no, execution_id, worker_id)
                   VALUES (%s, %s, 1, %s, 'worker')""", (user, job["id"], uuid4())),
            "outbox_job": (
                "INSERT INTO outbox(user_id, job_id) VALUES (%s, %s)", (user, job["id"])),
        }
        statement, args = statements[relationship]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with conn.transaction():
                await conn.execute(statement, args)


@pytest.mark.asyncio
@pytest.mark.parametrize("relationship", ["current_version", "job_version", "index_version"])
async def test_same_user_wrong_file_version_is_rejected(db_conn, relationship):
    async with db_conn.connection() as conn:
        file = await make_file(conn)
        other_file = await make_file(conn, path="other.pdf")
        version = await make_version(conn, file)
        statements = {
            "current_version": (
                "UPDATE files SET current_version_id = %s WHERE id = %s",
                (version["id"], other_file["id"])),
            "job_version": (
                """INSERT INTO jobs(user_id, file_id, file_version_id, job_type, pipeline_version)
                   VALUES (%s, %s, %s, 'index_file', '1')""",
                (OPERATOR, other_file["id"], version["id"])),
            "index_version": (
                """INSERT INTO file_index(user_id, path, file_id, file_version_id)
                   VALUES (%s, 'wrong.pdf', %s, %s)""",
                (OPERATOR, other_file["id"], version["id"])),
        }
        statement, args = statements[relationship]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with conn.transaction():
                await conn.execute(statement, args)


@pytest.mark.asyncio
async def test_legacy_index_rows_remain_supported(db_conn):
    async with db_conn.connection() as conn:
        row = await (await conn.execute(
            """INSERT INTO file_index(user_id, path, text_content)
               VALUES (%s, 'legacy.pdf', 'legacy text') RETURNING file_id, file_version_id""",
            (OPERATOR,),
        )).fetchone()
        assert row == {"file_id": None, "file_version_id": None}
        file = await make_file(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE file_index SET file_id = %s WHERE path = 'legacy.pdf'", (file["id"],),
                )


@pytest.mark.asyncio
async def test_job_and_outbox_rollback_together(db_conn):
    async with db_conn.connection() as conn:
        version = await make_version(conn, await make_file(conn))
        with pytest.raises(RuntimeError, match="interrupted"):
            async with conn.transaction():
                job = await make_job(conn, version)
                await conn.execute("INSERT INTO outbox(user_id, job_id) VALUES (%s, %s)",
                                   (OPERATOR, job["id"]))
                raise RuntimeError("interrupted")
        for table in ("jobs", "outbox"):
            row = await (await conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(
                sql.Identifier(table),
            ))).fetchone()
            assert row["n"] == 0


@pytest.mark.asyncio
async def test_deleting_owner_removes_file_versions_and_job_history(db_conn):
    async with db_conn.connection() as conn:
        user = str(uuid4())
        await conn.execute("INSERT INTO users(id, email) VALUES (%s, %s)", (user, f"{user}@test"))
        file = await make_file(conn, user_id=user)
        version = await make_version(conn, file)
        await conn.execute("UPDATE files SET current_version_id = %s WHERE id = %s",
                           (version["id"], file["id"]))
        job = await make_job(conn, version)
        await conn.execute("INSERT INTO outbox(user_id, job_id) VALUES (%s, %s)", (user, job["id"]))
        await conn.execute(
            """INSERT INTO job_attempts(user_id, job_id, attempt_no, execution_id, worker_id)
               VALUES (%s, %s, 1, %s, 'worker')""", (user, job["id"], uuid4()),
        )
        await conn.execute("DELETE FROM users WHERE id = %s", (user,))
        for table in ("files", "file_versions", "jobs", "job_attempts", "outbox"):
            row = await (await conn.execute(
                sql.SQL("SELECT count(*) AS n FROM {} WHERE user_id = %s").format(
                    sql.Identifier(table),
                ), (user,),
            )).fetchone()
            assert row["n"] == 0


def test_upgrade_preserves_existing_index_and_does_not_enable_new_rls(pg_url):
    """Apply the first B1 migration to a real pre-B1 database containing data."""
    name = "b1_upgrade_" + uuid4().hex
    dsn = pg_url.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(pg_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                runner.ensure_migrations_table(conn)
                migrations = sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql"))
                for migration in migrations:
                    if migration.name < MIGRATION:
                        runner.apply(conn, migration)
                conn.execute(
                    """INSERT INTO public.file_index(user_id, path, text_content, sha256)
                       VALUES (%s, 'old.pdf', 'existing searchable text', 'existing-checksum')""",
                    (OPERATOR,),
                )
                runner.apply(conn, next(m for m in migrations if m.name == MIGRATION))
                assert conn.execute(
                    "SELECT text_content, sha256, file_id, file_version_id FROM public.file_index"
                ).fetchone() == ("existing searchable text", "existing-checksum", None, None)
                assert conn.execute(
                    """SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'public' AND c.relrowsecurity
                         AND c.relname IN ('files', 'file_versions', 'jobs', 'job_attempts', 'outbox')"""
                ).fetchone()[0] == 0
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
