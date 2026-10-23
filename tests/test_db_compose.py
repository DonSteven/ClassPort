"""Validate credential separation using Compose's resolved service configuration."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is not installed")
@pytest.mark.parametrize("maintenance_file", [True, False])
def test_runtime_does_not_receive_maintenance_credentials(tmp_path, maintenance_file):
    root = Path(__file__).resolve().parents[1]
    shutil.copy(root / "docker-compose.yml", tmp_path)
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://app:runtime@db/study\nSESSION_SECRET=runtime-secret\nJOB_MAX_ATTEMPTS=2\n"
    )
    (tmp_path / ".env.coordinator").write_text(
        "DATABASE_URL=postgresql://coordinator:control@db/study\nJOB_MAX_ATTEMPTS=4\n"
    )
    job_settings = {"JOB_TIME_LIMIT": "180", "JOB_MAX_ATTEMPTS": "5", "JOB_REDELIVERY_SECONDS": "25"}
    (tmp_path / ".env.jobs").write_text("".join(f"{key}={value}\n" for key, value in job_settings.items()))
    (tmp_path / ".env.docker").write_text(
        "POSTGRES_USER=bootstrap\nPOSTGRES_PASSWORD=bootstrap-secret\nPOSTGRES_DB=study\n"
    )
    if maintenance_file:
        (tmp_path / ".env.migration").write_text(
            "MIGRATION_DATABASE_URL=postgresql://migrator:maintenance-secret@db/study\n"
            "APP_PASSWORD_HASH=$argon2id$v=19$literal-hash\n"
        )
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    command = ["docker", "compose", "-f", "docker-compose.yml", "--env-file", ".env.docker"]
    result = subprocess.run(
        [*command, "--profile", "*", "config", "--format", "json"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    runtime = services["classport"]["environment"]
    maintenance = services["migrate"].get("environment", {})
    assert runtime["DATABASE_URL"] == "postgresql://app:runtime@db/study"
    assert "SESSION_SECRET" in runtime
    assert not any(k.startswith("POSTGRES_") for k in runtime)
    assert "MIGRATION_DATABASE_URL" not in runtime
    assert "APP_PASSWORD_HASH" not in runtime
    assert "SESSION_SECRET" not in maintenance
    for name in ("classport", "worker", "dispatcher", "job-recovery"):
        assert {key: services[name]["environment"][key] for key in job_settings} == job_settings
    for name in ("dispatcher", "job-recovery"):
        assert "SESSION_SECRET" not in services[name]["environment"]
        assert services[name]["environment"]["DATABASE_URL"].startswith("postgresql://coordinator:")
    if maintenance_file:
        assert "maintenance-secret" in maintenance["MIGRATION_DATABASE_URL"]
        # `compose config` escapes dollars for its serialized output.
        assert maintenance["APP_PASSWORD_HASH"].replace("$$", "$") == "$argon2id$v=19$literal-hash"
    for name in ("classport", "migrate"):
        assert any(
            v["target"] == "/etc/classport/certs" and v["read_only"]
            for v in services[name]["volumes"]
        )
    assert "postgres" not in services["classport"].get("depends_on", {})
    result = subprocess.run(
        [*command, "config", "--services"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == {"classport", "frontend", "redis", "worker", "dispatcher", "job-recovery"}
