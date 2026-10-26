"""Measure real API/Redis/Celery processing locally using disposable data, never AWS."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
import pymupdf
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.processes import running_process, unused_port  # noqa: E402


def distribution(values):
    ordered = sorted(values)
    def percentile(fraction):
        position = (len(ordered)-1)*fraction
        lower = int(position)
        upper = min(lower+1, len(ordered)-1)
        return ordered[lower] + (ordered[upper]-ordered[lower])*(position-lower)
    return {'samples': len(values), 'p50': percentile(.5), 'p95': percentile(.95)} if values else {'samples': 0}


async def upload(client, path, content):
    response = await client.post('/api/files/upload-url', json={'path': path})
    response.raise_for_status()
    target = response.json()
    (await client.put(target['url'], content=content)).raise_for_status()
    response = await client.post(f"/api/files/{target['file_id']}/versions/{target['version_id']}/complete")
    response.raise_for_status()
    return response.json()['job_id']


async def finished(client, job_id):
    async with asyncio.timeout(90):
        while True:
            response = await client.get('/api/jobs/' + job_id)
            response.raise_for_status()
            state = response.json()
            if state['status'] in ('succeeded', 'failed', 'cancelled'):
                return state
            await asyncio.sleep(.1)


async def measure(args, dsn, broker, root):
    from argon2 import PasswordHasher
    with psycopg.connect(dsn) as conn:
        user = str(uuid4())
        conn.execute("INSERT INTO users(id,email,password_hash) VALUES (%s,'measure@example.test',%s)",
                     (user, PasswordHasher().hash('local-measurement-password')))
        conn.execute("CREATE ROLE measurement_app LOGIN PASSWORD 'local-only'")
        conn.execute('GRANT USAGE ON SCHEMA public TO measurement_app')
        conn.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO measurement_app')
        conn.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO measurement_app')
        conn.execute('REVOKE ALL ON _migrations FROM measurement_app')
        for table, in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND rowsecurity"):
            conn.execute(psycopg.sql.SQL('CREATE POLICY measurement_app ON {} TO measurement_app USING (true) WITH CHECK (true)').format(psycopg.sql.Identifier(table)))
        conn.execute("CREATE ROLE measurement_jobs LOGIN PASSWORD 'local-jobs-only'")
        conn.execute('GRANT USAGE ON SCHEMA public TO measurement_jobs')
        conn.execute('GRANT SELECT,INSERT,UPDATE ON jobs,job_attempts,outbox TO measurement_jobs')
    from psycopg.conninfo import conninfo_to_dict
    params = conninfo_to_dict(dsn)
    address = f"{params['host']}:{params['port']}/{params['dbname']}"
    app_dsn = 'postgresql://measurement_app:local-only@' + address
    jobs_dsn = 'postgresql://measurement_jobs:local-jobs-only@' + address
    settings = {'SESSION_SECRET': 'local-measurement-session', 'JOB_TIME_LIMIT': '120',
                'JOB_MAX_ATTEMPTS': '3', 'JOB_REDELIVERY_SECONDS': '60'}
    pdf = pymupdf.open()
    for page in range(args.pdf_pages):
        pdf.new_page().insert_text((72, 72), f'quantum mechanics measurement page {page}')
    pdf_bytes = pdf.tobytes()
    pdf.close()
    text_bytes = (b'quantum mechanics measurement\n' * (args.text_kib*1024//29+1))[:args.text_kib*1024]
    runs = []
    port = unused_port()
    with ExitStack() as processes:
        api = processes.enter_context(running_process('api', database_url=app_dsn, broker_url=broker,
                                      study_root=root, settings=settings, port=port))
        for mode in ('dispatch', 'recover'):
            processes.enter_context(running_process(mode, database_url=jobs_dsn, broker_url=broker,
                                                    study_root=root, settings=settings))
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=20) as client:
            async with asyncio.timeout(20):
                while True:
                    assert api.poll() is None, 'API did not start'
                    try:
                        if (await client.get('/api/health/ready')).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.1)
            login = await client.post('/api/auth/login', json={'email': 'measure@example.test', 'password': 'local-measurement-password'})
            login.raise_for_status()
            client.headers['Cookie'] = 'study_session=' + login.cookies['study_session']
            # Repeat both settings in each round.
            for repeat in range(args.repeats):
                for concurrency in args.concurrency:
                    with running_process('worker', database_url=app_dsn, broker_url=broker,
                                         study_root=root, settings=settings, concurrency=concurrency):
                        warmup = await finished(client, await upload(client, f'warmup-{repeat}-{concurrency}.txt', b'quantum warmup'))
                        assert warmup['status'] == 'succeeded', warmup
                        started = time.perf_counter()
                        ids = await asyncio.gather(*(upload(client, f'run-{repeat}-{concurrency}-{i}.' + ('pdf' if i % 2 else 'txt'),
                            pdf_bytes if i % 2 else text_bytes) for i in range(args.files)))
                        outcomes = await asyncio.gather(*(finished(client, job_id) for job_id in ids))
                        search = await client.get('/api/files/search', params={'q': 'quantum', 'limit': 100})
                        search.raise_for_status()
                        assert {state['file_id'] for state in outcomes if state['status'] == 'succeeded'} <= {hit['file_id'] for hit in search.json()}
                        elapsed = time.perf_counter()-started
                        with psycopg.connect(dsn, row_factory=dict_row) as conn:
                            rows = conn.execute("""SELECT j.id,j.status,j.error_code,j.attempt_count,v.size,v.content_type,
                                extract(epoch FROM j.started_at-j.next_run_at)::float AS queue_seconds,
                                extract(epoch FROM j.finished_at-j.started_at)::float AS processing_seconds,
                                extract(epoch FROM j.finished_at-j.created_at)::float AS task_seconds
                                FROM jobs j JOIN file_versions v ON v.id=j.file_version_id WHERE j.id=ANY(%s::uuid[])""", (ids,)).fetchall()
                        runs.append({'repeat': repeat+1, 'worker_concurrency': concurrency, 'batch_seconds': elapsed,
                            'files_per_second': len(rows)/elapsed, 'failure_count': sum(row['status'] != 'succeeded' for row in rows),
                            'retry_count': sum(row['attempt_count']-1 for row in rows),
                            'queue_seconds': distribution([r['queue_seconds'] for r in rows]),
                            'processing_seconds': distribution([r['processing_seconds'] for r in rows]),
                            'task_seconds': distribution([r['task_seconds'] for r in rows]),
                            'jobs': [{**row, 'id': str(row['id'])} for row in rows]})
    stages = []
    measured_ids = {job['id'] for run in runs for job in run['jobs']}
    for log in root.glob('worker-*.log'):
        for line in log.read_text().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if (entry.get('job_id') in measured_ids and entry.get('event') == 'operation.completed'
                    and entry.get('stage', '').startswith('index.')):
                stages.append({key: entry[key] for key in ('job_id', 'stage', 'duration_ms') if key in entry})
    return {'environment': 'local PostgreSQL 16 / Redis 7.4 / LocalStorage; no AWS',
            'measured_at': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'working_tree_changed': bool(subprocess.check_output(['git', 'diff', '--name-only'], cwd=ROOT, text=True).strip()),
            'settings': {key: value for key, value in settings.items() if key != 'SESSION_SECRET'},
            'workload': {'files_per_run': args.files, 'text_bytes': len(text_bytes), 'pdf_bytes': len(pdf_bytes), 'pdf_pages': args.pdf_pages},
            'runs': runs, 'stages': stages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files', type=int, default=6)
    parser.add_argument('--text-kib', type=int, default=256)
    parser.add_argument('--pdf-pages', type=int, default=5)
    parser.add_argument('--concurrency', nargs='+', type=int, default=[1, 2])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.files, args.text_kib, args.pdf_pages, args.repeats, *args.concurrency) < 1:
        parser.error('workload and concurrency values must be positive')
    with tempfile.TemporaryDirectory(prefix='classport-jobs-') as temporary, PostgresContainer('postgres:16-alpine') as pg, DockerContainer('redis:7.4-alpine').with_bind_ports(6379, ('127.0.0.1', None)) as queue:
        root = Path(temporary)
        dsn = pg.get_connection_url().replace('postgresql+psycopg2://', 'postgresql://')
        subprocess.run([sys.executable, str(ROOT / 'scripts/run_migrations.py')],
            env={'PATH': str(Path(sys.executable).parent), 'MIGRATION_DATABASE_URL': dsn}, check=True, capture_output=True)
        broker = f'redis://{queue.get_container_host_ip()}:{queue.get_exposed_port(6379)}/0'
        # Wait for readiness before allowing the independent publisher to connect.
        deadline = time.monotonic()+10
        while True:
            try:
                assert Redis.from_url(broker, socket_connect_timeout=1, socket_timeout=1).ping()
                break
            except (RedisConnectionError, RedisTimeoutError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(.1)
        report = asyncio.run(measure(args, dsn, broker, root))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'report': str(args.output), 'runs': len(report['runs']),
                          'jobs': sum(len(run['jobs']) for run in report['runs'])}))


if __name__ == '__main__':
    main()
