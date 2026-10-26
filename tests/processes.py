"""Real local processes shared by integration tests and the task measurement script."""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def unused_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


@contextmanager
def running_process(mode, *, database_url, broker_url, study_root, settings=None,
                    once=False, port=None, concurrency=1):
    environment = {'PATH': os.environ['PATH'], 'PYTHONPATH': str(ROOT),
                   'DATABASE_URL': database_url, 'CELERY_BROKER_URL': broker_url,
                   'STUDY_ROOT': str(study_root), 'DB_POOL_MIN_SIZE': '1', 'DB_POOL_MAX_SIZE': '2',
                   **(settings or {})}
    if mode == 'worker':
        command = [sys.executable, '-m', 'celery', '-A', 'app.celery_app:celery', 'worker',
                   '--pool=prefork', f'--concurrency={concurrency}', '--without-gossip', '--without-mingle']
    elif mode == 'api':
        command = [sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory',
                   '--host', '127.0.0.1', '--port', str(port)]
    else:
        command = [sys.executable, '-m', 'app.job_coordination', mode, *(['--once'] if once else [])]
    logs = Path(os.environ.get('PROCESS_LOG_DIR', study_root))
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / f'{mode}-{uuid4().hex}.log').open('w+') as output:
        child = subprocess.Popen(command, env=environment, cwd=study_root, stdout=output,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        failed = False
        try:
            yield child
        except BaseException:
            failed = True
            raise
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
            if failed or child.returncode not in (0, -signal.SIGKILL, -signal.SIGTERM):
                output.seek(0)
                print(f'{mode} exited {child.returncode}; log: {output.name}\n{output.read()[-6000:]}')
