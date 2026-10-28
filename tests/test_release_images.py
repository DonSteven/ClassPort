"""Run the published image pair against a disposable database before deployment.

Opt in with CLASSPORT_TEST_BACKEND_IMAGE, CLASSPORT_TEST_FRONTEND_IMAGE and
CLASSPORT_TEST_RELEASE_COMMIT.
The local release command supplies the exact registry digests, without rebuilding.
"""
import os
from contextlib import ExitStack
import subprocess
import time
from uuid import uuid4

import docker

from argon2 import PasswordHasher
import httpx
import psycopg
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.postgres import PostgresContainer


BACKEND = os.environ.get("CLASSPORT_TEST_BACKEND_IMAGE")
FRONTEND = os.environ.get("CLASSPORT_TEST_FRONTEND_IMAGE")
pytestmark = pytest.mark.skipif(not BACKEND or not FRONTEND, reason="release image digests not supplied")


def test_release_images_serve_login_api_and_mcp():
    expected_commit = os.environ["CLASSPORT_TEST_RELEASE_COMMIT"]
    for image in (BACKEND, FRONTEND):
        revision = subprocess.check_output(
            ["docker", "image", "inspect", "--format", '{{index .Config.Labels "org.opencontainers.image.revision"}}', image],
            text=True,
        ).strip()
        assert revision == expected_commit
    subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", BACKEND, "-ec",
         "test -f /app/release/docker-compose.prod.yml && test -f /app/release/deploy-prod.sh && test -f /app/release/bootstrap.py && test -f /app/release/recovery.py && test -f /app/scripts/smoke_restore.py"],
        check=True,
    )
    password = "disposable-image-check-password"
    token = "disposable-image-check-mcp-token"
    user_id = "00000000-0000-0000-0000-000000000001"
    with Network() as network, ExitStack() as background:
        volume = docker.from_env().volumes.create(name="classport-check-" + uuid4().hex)
        background.callback(volume.remove)
        database = PostgresContainer("postgres:16-alpine", username="migrator", password="testpw", dbname="image_test")
        with database.with_network(network).with_network_aliases("postgres") as pg:
            maintenance_dsn = "postgresql://migrator:testpw@postgres:5432/image_test"
            migrate = subprocess.run(
                ["docker", "run", "--rm", "--network", network.name, "-e", f"MIGRATION_DATABASE_URL={maintenance_dsn}",
                 BACKEND, "uv", "run", "--no-sync", "python", "scripts/run_migrations.py"],
                capture_output=True, text=True, timeout=90,
            )
            assert migrate.returncode == 0, migrate.stdout + migrate.stderr
            dsn = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
            with psycopg.connect(dsn) as conn:
                conn.execute("CREATE ROLE image_app LOGIN BYPASSRLS PASSWORD 'runtime-testpw'")
                conn.execute("GRANT USAGE ON SCHEMA public TO image_app")
                conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO image_app")
                conn.execute("REVOKE ALL ON public._migrations FROM image_app")
                conn.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO image_app")
                conn.execute("CREATE ROLE image_jobs LOGIN PASSWORD 'coordinator-testpw'")
                conn.execute("GRANT USAGE ON SCHEMA public TO image_jobs")
                conn.execute("GRANT SELECT, INSERT, UPDATE ON jobs, job_attempts, outbox TO image_jobs")
                conn.execute(
                    "UPDATE users SET email = %s, password_hash = %s WHERE id = %s",
                    ("release-check@example.test", PasswordHasher().hash(password), user_id),
                )
                conn.execute(
                    "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, token_endpoint_auth_method) "
                    "VALUES ('image-check', 'Image check', ARRAY['http://127.0.0.1/callback'], 'none')"
                )
                conn.execute(
                    "INSERT INTO oauth_tokens (user_id, token, client_id, scope, expires_at) "
                    "VALUES (%s, %s, 'image-check', 'mcp', now() + interval '1 hour')", (user_id, token),
                )
            redis = DockerContainer("redis:7.4-alpine").with_network(network).with_network_aliases("redis")
            background.enter_context(redis)
            backend = (DockerContainer(BACKEND).with_network(network).with_network_aliases("classport")
                       .with_volume_mapping(volume.name, "/opt/courses", "rw"))
            settings = {
                "DATABASE_URL": "postgresql://image_app:runtime-testpw@postgres:5432/image_test",
                "SESSION_SECRET": "disposable-image-check-session-secret", "PUBLIC_URL": "http://frontend",
                "STUDY_ROOT": "/opt/courses", "OPERATOR_EMAIL": "release-check@example.test",
            }
            for key, value in settings.items():
                backend.with_env(key, value)
            with backend.with_exposed_ports(8000) as api:
                ready = f"http://{api.get_container_host_ip()}:{api.get_exposed_port(8000)}/api/health/ready"
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        response = httpx.get(ready, timeout=3, trust_env=False)
                        if response.status_code == 200 and response.json().get("ok") is True:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    pytest.fail("release image did not become ready")
                # Accept and persist an authenticated upload while no worker or
                # coordinator exists, then let the independent processes pick it up.
                origin = f"http://{api.get_container_host_ip()}:{api.get_exposed_port(8000)}"
                with httpx.Client(base_url=origin, trust_env=False, timeout=10) as client:
                    login = client.post("/api/auth/login", json={"email": "release-check@example.test", "password": password})
                    login.raise_for_status()
                    client.headers["Cookie"] = "study_session=" + login.cookies["study_session"]
                    response = client.post("/api/files/upload-url", json={"path": "stopped-worker.txt"})
                    response.raise_for_status()
                    target = response.json()
                    client.put(target["url"], content=b"worker independence check").raise_for_status()
                    completion = client.post(f"/api/files/{target['file_id']}/versions/{target['version_id']}/complete")
                    completion.raise_for_status()
                    assert completion.json()["job_status"] == "queued"
                    queued = client.get("/api/jobs/" + completion.json()["job_id"])
                    queued.raise_for_status()
                    assert queued.json()["status"] == "queued"
                worker = (DockerContainer(BACKEND).with_network(network)
                          .with_volume_mapping(volume.name, "/opt/courses", "ro")
                          .with_command(["uv", "run", "--no-sync", "celery", "-A", "app.celery_app:celery",
                                         "worker", "--pool=prefork", "--concurrency=1"]))
                for key, value in {"DATABASE_URL": settings["DATABASE_URL"], "STUDY_ROOT": "/opt/courses",
                                   "CELERY_BROKER_URL": "redis://redis:6379/0",
                                   "DB_POOL_MIN_SIZE": "1", "DB_POOL_MAX_SIZE": "2"}.items():
                    worker.with_env(key, value)
                background.enter_context(worker)
                for mode in ("dispatch", "recover"):
                    coordinator = (DockerContainer(BACKEND).with_network(network)
                        .with_env("DATABASE_URL", "postgresql://image_jobs:coordinator-testpw@postgres:5432/image_test")
                        .with_env("CELERY_BROKER_URL", "redis://redis:6379/0")
                        .with_env("DB_POOL_MIN_SIZE", "1").with_env("DB_POOL_MAX_SIZE", "2")
                        .with_command(["uv", "run", "--no-sync", "python", "-m", "app.job_coordination", mode]))
                    background.enter_context(coordinator)
                frontend = DockerContainer(FRONTEND).with_network(network).with_network_aliases("frontend")
                with frontend:
                    # Caddy must be listening before the single authenticated probe.
                    for _ in range(30):
                        if frontend.exec(["wget", "--spider", "-q", "http://localhost/login"])[0] == 0:
                            break
                        time.sleep(1)
                    else:
                        pytest.fail("frontend image did not start")
                    command = [
                        "docker", "run", "--rm", "--network", network.name,
                        "-e", "SMOKE_BASE_URL=http://frontend", "-e", "SMOKE_EMAIL=release-check@example.test",
                        "-e", f"SMOKE_PASSWORD={password}", "-e", f"SMOKE_MCP_TOKEN={token}",
                        BACKEND, "uv", "run", "--no-sync", "python", "scripts/smoke_release.py",
                    ]
                    smoke = subprocess.run(
                        command,
                        capture_output=True, text=True, timeout=90,
                    )
                    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
                    assert "smoke: PASS" in smoke.stdout
                    command[command.index(f"SMOKE_MCP_TOKEN={token}")] = "SMOKE_MCP_TOKEN=invalid-token"
                    rejected = subprocess.run(command, capture_output=True, text=True, timeout=90)
                    assert rejected.returncode == 1, "unauthenticated MCP must fail the release gate"
                    assert "smoke: PASS" not in rejected.stdout
                    assert password not in rejected.stdout + rejected.stderr
                # Scheduled backups require the API to drain and exit cleanly.
                api_container = api.get_wrapped_container()
                api_container.stop(timeout=30)
                api_container.reload()
                assert not api_container.attrs["State"]["Running"]
                assert api_container.attrs["State"]["ExitCode"] in {0, 143}
