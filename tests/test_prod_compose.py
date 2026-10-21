"""Resolve the standalone production file without accessing real credentials."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose not installed")
def test_production_uses_only_pinned_images_and_keeps_credentials_separate(tmp_path):
    root = Path(__file__).resolve().parents[1]
    snapshot = tmp_path / "releases/example"
    snapshot.mkdir(parents=True)
    shutil.copy(root / "docker-compose.prod.yml", snapshot)
    shutil.copy(root / ".env.release.example", snapshot / "release.env")
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://app:runtime@rds/study\nSESSION_SECRET=test\n")
    (tmp_path / ".env.migration").write_text("MIGRATION_DATABASE_URL=postgresql://admin:maintenance@rds/study\n")
    (tmp_path / ".env.smoke").write_text("SMOKE_PASSWORD=smoke-secret\nSMOKE_MCP_TOKEN=smoke-token\n")
    # An accidental local override must have no effect on the explicit file.
    (tmp_path / "docker-compose.override.yml").write_text("services:\n  postgres:\n    image: postgres:16-alpine\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    for key in ("BACKEND_IMAGE", "FRONTEND_IMAGE"):
        env.pop(key, None)
    command = ["docker", "compose", "--project-directory", str(tmp_path),
               "-f", str(snapshot / "docker-compose.prod.yml"),
               "--env-file", str(snapshot / "release.env")]
    result = subprocess.run(
        [*command, "--profile", "*", "config", "--format", "json"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    )
    services = json.loads(result.stdout)["services"]
    assert set(services) == {"classport", "frontend", "migrate", "smoke"}
    assert all("build" not in service and "@sha256:" in service["image"] for service in services.values())
    assert services["classport"]["image"] == services["migrate"]["image"] == services["smoke"]["image"]
    runtime = services["classport"]["environment"]
    assert "MIGRATION_DATABASE_URL" not in runtime
    assert "SMOKE_PASSWORD" not in runtime and "SMOKE_MCP_TOKEN" not in runtime
    assert "DATABASE_URL" not in services["migrate"]["environment"]
    assert "MIGRATION_DATABASE_URL" not in services["smoke"]["environment"]
    assert "ports" not in services["classport"]
    storage = next(v for v in services["classport"]["volumes"] if v["target"] == "/opt/courses")
    assert storage["type"] == "bind" and storage["source"] == str(tmp_path / "data/courses")
    assert storage.get("bind", {}).get("create_host_path", False) is False
    for name in ("classport", "migrate"):
        assert any(v["target"] == "/etc/classport/certs" and v["read_only"] for v in services[name]["volumes"])
    result = subprocess.run([*command, "config", "--services"], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert set(result.stdout.split()) == {"classport", "frontend"}
