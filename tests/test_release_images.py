"""Run the published image pair against a disposable database before deployment.

Opt in with CLASSPORT_TEST_BACKEND_IMAGE, CLASSPORT_TEST_FRONTEND_IMAGE and
CLASSPORT_TEST_RELEASE_COMMIT.
The deployment workflow supplies the exact registry digests, without rebuilding.
"""
import os
import subprocess
import time

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
         "test -f /app/release/docker-compose.prod.yml && test -f /app/release/deploy-prod.sh && test -f /app/release/bootstrap.py"],
        check=True,
    )
    password = "disposable-image-check-password"
    token = "disposable-image-check-mcp-token"
    user_id = "00000000-0000-0000-0000-000000000001"
    with Network() as network:
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
            backend = DockerContainer(BACKEND).with_network(network).with_network_aliases("classport")
            settings = {
                "DATABASE_URL": "postgresql://image_app:runtime-testpw@postgres:5432/image_test",
                "SESSION_SECRET": "disposable-image-check-session-secret", "PUBLIC_URL": "http://frontend",
                "STUDY_ROOT": "/app", "OPERATOR_EMAIL": "release-check@example.test",
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
