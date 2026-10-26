"""Committed data and actual Redis/Celery/coordinator processes; no eager tasks."""
import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import time
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
import pytest
import pytest_asyncio
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from testcontainers.core.container import DockerContainer

from tests.processes import running_process, unused_port

from app import db
from app.services import file_records as files, jobs, file_index

@pytest.fixture(scope="module")
def broker_container():
    with DockerContainer("redis:7.4-alpine").with_bind_ports(6379, ("127.0.0.1", unused_port())) as redis:
        yield redis


@pytest.fixture(scope="module")
def broker(broker_container):
    redis = broker_container
    url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
    with Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1) as client:
        deadline = time.monotonic() + 10
        while True:
            try:
                assert client.ping()
                break
            except (RedisConnectionError, RedisTimeoutError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
    yield url


@pytest.fixture(scope="module")
def roles(pg_url):
    names = ["worker_" + uuid4().hex[:12], "coordinator_" + uuid4().hex[:12]]
    with psycopg.connect(pg_url, autocommit=True) as conn:
        for name in names:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'test-only' NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(name)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(names[0])))
        conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(names[0])))
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
        def process(mode, *, once=False, settings=None, port=None):
            with running_process(mode, database_url=roles["worker" if mode in ("worker", "api") else "coordinator"],
                                 broker_url=broker, study_root=tmp_path, once=once,
                                 settings=settings, port=port) as child:
                processes.append(child)
                yield child

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
            with process("dispatch") as child:
                try:
                    async with asyncio.timeout(15):
                        while not redis.llen("file-processing"):
                            assert child.poll() is None, "dispatcher exited before publishing"
                            await asyncio.sleep(0.1)
                except (AssertionError, TimeoutError):
                    state = await db.fetchrow(
                        """SELECT j.status AS job_status, j.next_run_at, o.status AS outbox_status,
                                  o.available_at, clock_timestamp() AS database_time
                           FROM jobs j JOIN outbox o ON o.job_id=j.id AND o.user_id=j.user_id
                           WHERE j.id=%s""", upload["job_id"],
                    )
                    print(f"Dispatch interruption state: {state}; Redis queue: {redis.llen('file-processing')}")
                    raise
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
    # Run dispatch continuously, as in production, while retry backoff elapses.
    with process("dispatch"), process("worker"):
        state = await wait_status(user, upload["job_id"])
    assert state["attempt_count"] == 2
    assert await db.fetchval("SELECT status FROM job_attempts WHERE job_id=%s AND attempt_no=1", upload["job_id"]) == "expired"


async def test_coordinator_credentials_cannot_read_content_credentials_or_create_schema(roles):
    with psycopg.connect(roles["coordinator"]) as conn:
        for query in ("SELECT * FROM users", "SELECT * FROM user_secrets", "SELECT * FROM file_index",
                      "SELECT * FROM files", "CREATE TABLE unwanted_worker_table(id int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(query)


async def wait_api(client, child):
    import httpx
    async with asyncio.timeout(20):
        while True:
            assert child.poll() is None, 'API exited before readiness'
            try:
                if (await client.get('/api/health/ready')).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)


@pytest_asyncio.fixture
async def api(environment):
    from argon2 import PasswordHasher
    import httpx
    from tests.processes import unused_port
    user, process = environment
    await db.execute('UPDATE users SET password_hash=%s WHERE id=%s', PasswordHasher().hash('process-password'), user)
    port = unused_port()
    with process('api', port=port, settings={'SESSION_SECRET': 'process-test-session'}) as child:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=15) as client:
            await wait_api(client, child)
            login = await client.post('/api/auth/login', json={'email': f'{user}@test', 'password': 'process-password'})
            login.raise_for_status()
            client.headers['Cookie'] = 'study_session=' + login.cookies['study_session']
            yield client, child, port


async def api_upload(client, path, content):
    response = await client.post('/api/files/upload-url', json={'path': path})
    response.raise_for_status()
    target = response.json()
    (await client.put(target['url'], content=content)).raise_for_status()
    response = await client.post(f"/api/files/{target['file_id']}/versions/{target['version_id']}/complete")
    response.raise_for_status()
    return response.json()


async def test_api_accepts_while_redis_is_down_then_processes(environment, api, broker_container):
    user, process = environment
    client, _, _ = api
    container = broker_container.get_wrapped_container()
    await asyncio.to_thread(container.stop)
    try:
        with process('dispatch'):
            uploaded = await api_upload(client, 'offline.txt', b'quantum offline notification')
            assert uploaded['job_status'] == 'queued'
            assert (await client.get('/api/health/ready')).status_code == 200
            assert await db.fetchval("SELECT count(*) FROM outbox WHERE job_id=%s AND status='pending'", uploaded['job_id']) == 1
    finally:
        await asyncio.to_thread(container.start)
    with process('dispatch'), process('worker'):
        await wait_status(user, uploaded['job_id'])
    assert len(await file_index.search(user, 'quantum')) == 1


@pytest.mark.parametrize('committed', [False, True])
async def test_api_termination_around_commit_preserves_acceptance(environment, api, pg_url, committed):
    import httpx
    user, process = environment
    client, child, port = api
    target_response = await client.post('/api/files/upload-url', json={'path': 'interrupted.txt'})
    target_response.raise_for_status()
    target = target_response.json()
    (await client.put(target['url'], content=b'quantum interrupted upload')).raise_for_status()
    completion = f"/api/files/{target['file_id']}/versions/{target['version_id']}/complete"
    table = 'events' if committed else 'outbox'
    with psycopg.connect(pg_url, autocommit=True) as guard:
        guard.execute('SELECT pg_advisory_lock(7901326)')
        guard.execute(sql.SQL("""CREATE FUNCTION test_pause_completion() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.user_id={}::uuid THEN PERFORM pg_advisory_xact_lock(7901326); END IF;
            RETURN NEW; END $$""").format(sql.Literal(str(user))))
        guard.execute(sql.SQL('CREATE TRIGGER test_pause_completion BEFORE INSERT ON {} FOR EACH ROW EXECUTE FUNCTION test_pause_completion()').format(sql.Identifier(table)))
        request = asyncio.create_task(client.post(completion))
        try:
            async with asyncio.timeout(10):
                while not await db.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)))', guard.info.backend_pid):
                    assert not request.done(), 'completion returned before the commit boundary was observed'
                    await asyncio.sleep(0.05)
            assert await db.fetchval('SELECT count(*) FROM jobs WHERE file_id=%s', target['file_id']) == int(committed)
            os.killpg(child.pid, signal.SIGKILL)
            await asyncio.to_thread(child.wait, 5)
            with pytest.raises(httpx.HTTPError):
                await request
        finally:
            guard.execute('SELECT pg_advisory_unlock(7901326)')
            guard.execute(sql.SQL('DROP TRIGGER test_pause_completion ON {}').format(sql.Identifier(table)))
            guard.execute('DROP FUNCTION test_pause_completion()')
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
    with process('api', port=port, settings={'SESSION_SECRET': 'process-test-session'}) as replacement:
        await wait_api(client, replacement)
        response = await client.post(completion)
        response.raise_for_status()
        job_id = response.json()['job_id']
        assert await db.fetchval('SELECT count(*) FROM jobs WHERE file_id=%s', target['file_id']) == 1
        assert await db.fetchval('SELECT count(*) FROM outbox WHERE job_id=%s', job_id) == 1
        with process('dispatch'), process('worker'):
            await wait_status(user, job_id)
        assert len(await file_index.search(user, 'quantum')) == 1


async def test_hard_timeout_and_natural_lease_recovery_use_shared_settings(environment, pg_url):
    user, process = environment
    settings = {'JOB_TIME_LIMIT': '2', 'JOB_MAX_ATTEMPTS': '2', 'JOB_REDELIVERY_SECONDS': '3'}
    uploaded = await files.upload(user, 'timeout.txt', b'quantum timeout recovery')
    with process('dispatch', settings=settings), process('worker', settings=settings), process('recover', settings=settings):
        async with await psycopg.AsyncConnection.connect(pg_url) as locked:
            await locked.execute('SELECT id FROM files WHERE id=%s FOR UPDATE', (uploaded['file_id'],))
            await wait_status(user, uploaded['job_id'], 'running')
            first = await db.fetchrow('SELECT * FROM jobs WHERE id=%s', uploaded['job_id'])
            assert 31 < (first['lease_until']-first['started_at']).total_seconds() < 33
            worker = await db.fetchval('SELECT worker_id FROM job_attempts WHERE job_id=%s', uploaded['job_id'])
            pid = int(worker.rsplit(':', 1)[1])
            async with asyncio.timeout(10):
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(0.1)
        # Let the database lease expire naturally; no test writes to lease_until.
        async with asyncio.timeout(50):
            while (await jobs.get(user, uploaded['job_id']))['status'] != 'succeeded':
                assert (await jobs.get(user, uploaded['job_id']))['status'] != 'failed'
                await asyncio.sleep(0.2)
    assert (await jobs.get(user, uploaded['job_id']))['attempt_count'] == 2
    assert await db.fetchval("SELECT status FROM job_attempts WHERE job_id=%s AND attempt_no=1", uploaded['job_id']) == 'expired'


async def test_recovery_uses_nondefault_attempt_budget(environment, pg_url):
    user, process = environment
    uploaded = await files.upload(user, 'budget.txt', b'quantum budget')
    # A real worker claims the job, then loses its process while blocked on this file.
    async with await psycopg.AsyncConnection.connect(pg_url) as locked:
        await locked.execute('SELECT id FROM files WHERE id=%s FOR UPDATE', (uploaded['file_id'],))
        await coordinate(process, 'dispatch')
        with process('worker', settings={'JOB_MAX_ATTEMPTS': '1'}) as child:
            await wait_status(user, uploaded['job_id'], 'running')
            os.killpg(child.pid, signal.SIGKILL)
            await asyncio.to_thread(child.wait, 5)
        await db.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s", uploaded['job_id'])
        with process('recover', once=True, settings={'JOB_MAX_ATTEMPTS': '1'}) as child:
            assert await asyncio.to_thread(child.wait, 15) == 0
    assert (await jobs.get(user, uploaded['job_id']))['status'] == 'failed'


async def test_two_users_rest_mcp_and_workers_share_no_content(environment, api):
    from argon2 import PasswordHasher
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    user, process = environment
    client, _, _ = api
    other = uuid4()
    client_id = 'process-' + uuid4().hex
    tokens = [uuid4().hex, uuid4().hex]
    await db.execute('INSERT INTO users(id,email,password_hash) VALUES (%s,%s,%s)', other, f'{other}@test', PasswordHasher().hash('other-password'))
    await db.execute("INSERT INTO oauth_clients(client_id,client_name,redirect_uris,token_endpoint_auth_method) VALUES (%s,'Process test',ARRAY['http://127.0.0.1/callback'],'none')", client_id)
    for owner, token in zip((user, other), tokens):
        await db.execute("INSERT INTO oauth_tokens(user_id,token,client_id,scope,expires_at) VALUES (%s,%s,%s,'mcp',clock_timestamp()+interval '1 hour')", owner, token, client_id)
    try:
        async with httpx.AsyncClient(base_url=client.base_url, timeout=15) as second:
            login = await second.post('/api/auth/login', json={'email': f'{other}@test', 'password': 'other-password'})
            login.raise_for_status()
            second.headers['Cookie'] = 'study_session=' + login.cookies['study_session']
            with process('dispatch'), process('worker'):
                first_upload, second_upload = await asyncio.gather(
                    api_upload(client, 'lecture.txt', b'quantum owneralpha'),
                    api_upload(second, 'lecture.txt', b'relativity ownerbeta'))
                await asyncio.gather(wait_status(user, first_upload['job_id']), wait_status(other, second_upload['job_id']))
            for caller, foreign in ((client, second_upload), (second, first_upload)):
                assert (await caller.get('/api/jobs/' + foreign['job_id'])).status_code == 404
                assert (await caller.post('/api/jobs/' + foreign['job_id'] + '/retry')).status_code == 404
                assert (await caller.get('/api/files/raw', params={'file_id': foreign['file_id'], 'version_id': foreign['version_id']})).status_code == 404
            assert (await client.get('/api/files/search', params={'q': 'relativity'})).json() == []
            assert (await second.get('/api/files/search', params={'q': 'quantum'})).json() == []
            async def read(token):
                async with streamablehttp_client(str(client.base_url.join('/mcp')), headers={'Authorization': 'Bearer ' + token}, timeout=10) as (incoming, outgoing, _):
                    async with ClientSession(incoming, outgoing) as session:
                        await session.initialize()
                        result = await session.call_tool('read_course_file', {'path': 'lecture.txt'})
                        assert not result.isError
                        return str(result.content)
            first_content, second_content = await asyncio.gather(*(read(token) for token in tokens))
            assert 'owneralpha' in first_content and 'ownerbeta' not in first_content
            assert 'ownerbeta' in second_content and 'owneralpha' not in second_content
    finally:
        await db.execute('DELETE FROM oauth_tokens WHERE client_id=%s', client_id)
        await db.execute('DELETE FROM oauth_clients WHERE client_id=%s', client_id)
        await db.execute('DELETE FROM users WHERE id=%s', other)


async def test_real_worker_reports_permanent_errors_and_only_publishes_current_files(environment, tmp_path):
    import json
    user, process = environment
    broken = await files.upload(user, 'broken.pdf', b'invalid PDF')
    old = await files.upload(user, 'lecture.txt', b'oldcontent')
    current = await files.upload(user, 'lecture.txt', b'quantum currentcontent')
    await files.move(user, 'lecture.txt', 'renamed.txt')
    deleted = await files.upload(user, 'deleted.txt', b'deletedcontent')
    await files.delete(user, ['deleted.txt'])
    unsupported = await files.upload(user, 'unsupported.txt', b'unsupportedcontent')
    await db.execute("UPDATE jobs SET pipeline_version='unknown' WHERE id=%s", unsupported['job_id'])
    with process('dispatch'), process('worker'):
        await wait_status(user, current['job_id'])
        await wait_status(user, broken['job_id'], 'failed')
        await wait_status(user, unsupported['job_id'], 'failed')
    assert (await jobs.get(user, broken['job_id']))['error_code'] == 'invalid_pdf'
    assert (await jobs.get(user, broken['job_id']))['can_retry'] is False
    assert (await jobs.get(user, unsupported['job_id']))['error_code'] == 'unsupported_pipeline'
    assert (await jobs.get(user, old['job_id']))['status'] == 'cancelled'
    assert (await jobs.get(user, deleted['job_id']))['status'] == 'cancelled'
    assert (await file_index.search(user, 'currentcontent'))[0]['path'] == 'renamed.txt'
    assert await file_index.search(user, 'oldcontent') == []
    assert await file_index.search(user, 'deletedcontent') == []
    records = []
    for path in Path(os.environ.get('PROCESS_LOG_DIR', tmp_path)).glob('worker-*.log'):
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('job_id') == current['job_id']:
                records.append(row)
    claimed = next(row for row in records if row['event'] == 'job.claimed')
    succeeded = [row for row in records if row['event'] == 'job.succeeded']
    assert claimed['queue_wait_ms'] >= 0
    assert len(succeeded) == 1 and succeeded[0]['processing_ms'] > 0
    assert succeeded[0]['end_to_end_ms'] >= succeeded[0]['processing_ms']
    stages = {row.get('stage') for row in records}
    assert {'index.read', 'index.extract', 'index.publish'} <= stages
