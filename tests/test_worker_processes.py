"""Committed data and actual Redis/Celery/coordinator processes; no eager tasks."""
import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
import pytest
import pytest_asyncio
from redis import Redis
from testcontainers.core.container import DockerContainer

from app import db
from app.services import file_records as files, jobs, file_index

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def broker():
    with DockerContainer("redis:7.4-alpine").with_bind_ports(6379, ("127.0.0.1", None)) as redis:
        url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
        yield url


@pytest.fixture(scope="module")
def roles(pg_url):
    names = ["worker_" + uuid4().hex[:12], "coordinator_" + uuid4().hex[:12]]
    with psycopg.connect(pg_url, autocommit=True) as conn:
        for name in names:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'test-only' NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(name)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(names[0])))
        conn.execute(sql.SQL("REVOKE ALL ON public._migrations FROM {}").format(sql.Identifier(names[0])))
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE ON jobs, job_attempts, outbox TO {}").format(sql.Identifier(names[1])))
        for table, in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND rowsecurity"):
            conn.execute(sql.SQL("CREATE POLICY {} ON {} TO {} USING (true) WITH CHECK (true)").format(
                sql.Identifier(names[0]), sql.Identifier(table), sql.Identifier(names[0])))
    params = conninfo_to_dict(pg_url)
    yield {kind: f"postgresql://{name}:test-only@{params['host']}:{params['port']}/{params['dbname']}"
           for kind, name in zip(("worker", "coordinator"), names)}
    with psycopg.connect(pg_url, autocommit=True) as conn:
        for name in names:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


@pytest_asyncio.fixture
async def environment(pg_url, roles, broker, tmp_path, monkeypatch):
    Redis.from_url(broker).flushdb()
    async with AsyncConnectionPool(pg_url, min_size=1, max_size=3, open=False,
                                   kwargs={"row_factory": dict_row}, configure=db._configure_connection) as pool:
        monkeypatch.setattr(db, "_pool", pool)
        monkeypatch.setenv("STUDY_ROOT", str(tmp_path))
        user = uuid4()
        await db.execute("INSERT INTO users(id,email) VALUES (%s,%s)", user, f"{user}@test")
        processes = []

        @contextmanager
        def process(mode, *, once=False):
            env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT),
                   "DATABASE_URL": roles["worker" if mode == "worker" else "coordinator"],
                   "CELERY_BROKER_URL": broker, "STUDY_ROOT": str(tmp_path),
                   "DB_POOL_MIN_SIZE": "1", "DB_POOL_MAX_SIZE": "2"}
            # No .env file or SESSION_SECRET exists in this process's directory.
            command = ([sys.executable, "-m", "celery", "-A", "app.celery_app:celery", "worker",
                        "--pool=prefork", "--concurrency=1", "--without-gossip", "--without-mingle"]
                       if mode == "worker" else [sys.executable, "-m", "app.job_coordination", mode,
                                                  *(["--once"] if once else [])])
            with (tmp_path / f"{mode}-{uuid4().hex}.log").open("w+") as output:
                proc = subprocess.Popen(command, env=env, cwd=tmp_path, stdout=output,
                                        stderr=subprocess.STDOUT, start_new_session=True)
                processes.append(proc)
                try:
                    yield proc
                finally:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait(timeout=5)
                    output.seek(0)
                    if proc.returncode not in (0, -signal.SIGKILL, -signal.SIGTERM):
                        print(output.read()[-6000:])

        try:
            yield user, process
        finally:
            assert all(p.poll() is not None for p in processes), "test left a background process running"
            await db.execute("DELETE FROM users WHERE id=%s", user)


async def wait_status(user, job_id, status="succeeded"):
    async with asyncio.timeout(25):
        while True:
            state = await jobs.get(user, job_id)
            if state["status"] == status:
                return state
            assert state["status"] not in ("failed", "cancelled"), state
            await asyncio.sleep(0.1)


async def coordinate(process, mode):
    with process(mode, once=True) as child:
        assert await asyncio.to_thread(child.wait, 15) == 0


async def test_stopped_worker_queued_then_real_worker_handles_two_users_and_reuses_loop(environment):
    user, process = environment
    other = uuid4()
    await db.execute("INSERT INTO users(id,email) VALUES (%s,%s)", other, f"{other}@test")
    try:
        first = await files.upload(user, "lecture.txt", b"quantum mechanics")
        second = await files.upload(other, "lecture.txt", b"relativity principles")
        assert (await jobs.get(user, first["job_id"]))["status"] == "queued"
        await coordinate(process, "dispatch")
        with process("worker"):
            await wait_status(user, first["job_id"])
            await wait_status(other, second["job_id"])
            third = await files.upload(user, "more.txt", b"quantum chemistry")
            await coordinate(process, "dispatch")
            await wait_status(user, third["job_id"])
        workers = await db.fetch("SELECT DISTINCT worker_id FROM job_attempts WHERE user_id=%s", user)
        assert len(workers) == 1 and not workers[0]["worker_id"].endswith(f":{os.getpid()}")
        assert len(await file_index.search(user, "quantum")) == 2
        assert await file_index.search(other, "quantum") == []
    finally:
        await db.execute("DELETE FROM users WHERE id=%s", other)


async def test_redis_loses_notifications_and_database_rebuilds_them(environment, broker):
    user, process = environment
    upload = await files.upload(user, "notes.txt", b"quantum mechanics")
    await coordinate(process, "dispatch")
    Redis.from_url(broker).flushdb()  # This disposable test broker has no other clients.
    await db.execute("UPDATE outbox SET created_at=clock_timestamp()-interval '2 minutes' WHERE job_id=%s", upload["job_id"])
    await coordinate(process, "recover")
    await coordinate(process, "dispatch")
    with process("worker"):
        await wait_status(user, upload["job_id"])
    assert await db.fetchval("SELECT count(*) FROM job_attempts WHERE job_id=%s", upload["job_id"]) == 1


async def test_dispatcher_killed_after_send_can_resend_without_duplicate_execution(environment, pg_url, broker):
    user, process = environment
    upload = await files.upload(user, "notes.txt", b"quantum mechanics")
    redis = Redis.from_url(broker)
    # Pause only the test database's sent-marker update. The actual dispatcher
    # publishes to real Redis, then is killed while its transaction is uncommitted.
    with psycopg.connect(pg_url, autocommit=True) as guard:
        guard.execute("SELECT pg_advisory_lock(7901325)")
        guard.execute(sql.SQL("""CREATE FUNCTION test_pause_dispatch() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              IF NEW.job_id = {}::uuid THEN PERFORM pg_advisory_xact_lock(7901325); END IF;
              RETURN NEW;
            END $$""").format(sql.Literal(upload["job_id"])))
        guard.execute("CREATE TRIGGER test_pause_dispatch BEFORE UPDATE ON outbox FOR EACH ROW EXECUTE FUNCTION test_pause_dispatch()")
        try:
            with process("dispatch", once=True) as child:
                async with asyncio.timeout(15):
                    while not redis.llen("file-processing"):
                        assert child.poll() is None
                        await asyncio.sleep(0.1)
                os.killpg(child.pid, signal.SIGKILL)
                await asyncio.to_thread(child.wait, 5)
        finally:
            guard.execute("SELECT pg_advisory_unlock(7901325)")
            guard.execute("DROP TRIGGER test_pause_dispatch ON outbox")
            guard.execute("DROP FUNCTION test_pause_dispatch()")
    assert await db.fetchval("SELECT status FROM outbox WHERE job_id=%s", upload["job_id"]) == "pending"
    await coordinate(process, "dispatch")
    assert redis.llen("file-processing") == 2
    with process("worker"):
        await wait_status(user, upload["job_id"])
    assert await db.fetchval("SELECT count(*) FROM job_attempts WHERE job_id=%s", upload["job_id"]) == 1


async def test_killed_worker_is_recovered_by_an_independent_process(environment, pg_url):
    user, process = environment
    upload = await files.upload(user, "notes.txt", b"quantum mechanics")
    async with await psycopg.AsyncConnection.connect(pg_url) as locked:
        await locked.execute("SELECT id FROM files WHERE id=%s FOR UPDATE", (upload["file_id"],))
        await coordinate(process, "dispatch")
        with process("worker") as child:
            await wait_status(user, upload["job_id"], "running")
            os.killpg(child.pid, signal.SIGKILL)
            await asyncio.to_thread(child.wait, 5)
        await db.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s", upload["job_id"])
        await coordinate(process, "recover")
    assert await db.fetchval("SELECT count(*) FROM file_index WHERE file_id=%s", upload["file_id"]) == 0
    await asyncio.sleep(5)
    await coordinate(process, "dispatch")
    with process("worker"):
        state = await wait_status(user, upload["job_id"])
    assert state["attempt_count"] == 2
    assert await db.fetchval("SELECT status FROM job_attempts WHERE job_id=%s AND attempt_no=1", upload["job_id"]) == "expired"


async def test_coordinator_credentials_cannot_read_content_credentials_or_create_schema(roles):
    with psycopg.connect(roles["coordinator"]) as conn:
        for query in ("SELECT * FROM users", "SELECT * FROM user_secrets", "SELECT * FROM file_index",
                      "SELECT * FROM files", "CREATE TABLE unwanted_worker_table(id int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(query)
