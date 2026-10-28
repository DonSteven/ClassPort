"""Exercise deployment ordering with fake container and HTTP commands."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("external", [False, True])
def test_deploy_runs_maintenance_separately_and_skips_local_db_when_external(tmp_path, external):
    root = Path(__file__).resolve().parents[1]
    shutil.copy(root / "deploy.sh", tmp_path)
    for name in ("Dockerfile", "docker-compose.yml", ".env", ".env.docker", ".env.migration", ".env.coordinator", ".env.jobs"):
        (tmp_path / name).touch()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    seed = scripts / "seed_operator_password.py"
    seed.touch(mode=0o755)
    commands = tmp_path / "commands.log"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DB_DEPLOY_TEST_LOG"\n'
        'case "$*" in\n'
        '  *"images classport --format"*) echo test-image ;;\n'
        '  inspect*) echo healthy ;;\n'
        'esac\n'
    )
    docker.chmod(0o755)
    curl = binaries / "curl"
    curl.write_text(
        '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$DB_DEPLOY_TEST_LOG"\n'
        'echo \'{"ok":true}\'\n'
    )
    curl.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    env.update(PATH=f"{binaries}:{os.environ['PATH']}", DB_DEPLOY_TEST_LOG=str(commands))
    result = subprocess.run(
        ["bash", str(tmp_path / "deploy.sh"), *(["--external-db"] if external else [])],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = commands.read_text().splitlines()
    assert "curl -fsS --max-time 3 http://127.0.0.1:8000/api/health" in calls
    migrate = next(i for i, c in enumerate(calls) if c.endswith("run --rm --no-deps migrate"))
    seed = next(i for i, c in enumerate(calls) if "scripts/seed_operator_password.py" in c)
    start = next(i for i, c in enumerate(calls) if c.endswith("up -d classport redis worker dispatcher job-recovery storage-maintenance"))
    assert migrate < seed < start
    assert all("run --rm --no-deps classport" not in c for c in calls)
    if external:
        assert all("up -d postgres" not in c and "classport-postgres" not in c for c in calls)
        assert all("--profile local-db" not in c for c in calls)
    else:
        postgres = next(i for i, c in enumerate(calls) if c.endswith("up -d postgres"))
        assert postgres < migrate


@pytest.mark.parametrize("previous_worker", [False, True])
def test_local_rollback_restores_frontend_and_only_supported_background_processes(tmp_path, previous_worker):
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / "deploy.sh"
    script.write_text((root / "deploy.sh").read_text().replace("HEALTH_TIMEOUT=60", "HEALTH_TIMEOUT=1"))
    for name in ("Dockerfile", "docker-compose.yml", ".env", ".env.docker", ".env.migration", ".env.coordinator", ".env.jobs"):
        (tmp_path / name).touch()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    commands = tmp_path / "commands.log"
    docker = binaries / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DB_DEPLOY_TEST_LOG"\n'
        'case "$*" in\n'
        '  *"images classport --format"*) echo old-backend ;;\n'
        '  *"images frontend --format"*) echo old-frontend ;;\n'
        '  "tag classport:previous classport:latest") touch "$DB_DEPLOY_ROLLED_BACK" ;;\n'
        f'  "image inspect"*) echo {1 if previous_worker else "missing"} ;;\n'
        'esac\n'
    )
    docker.chmod(0o755)
    curl = binaries / "curl"
    curl.write_text('#!/bin/sh\n[ -f "$DB_DEPLOY_ROLLED_BACK" ] && echo \'{"ok":true}\'\n')
    curl.chmod(0o755)
    result = subprocess.run(["bash", str(script), "--external-db"], cwd=tmp_path,
        env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
             "DB_DEPLOY_TEST_LOG": str(commands), "DB_DEPLOY_ROLLED_BACK": str(tmp_path / "rolled-back")},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 1 and "rollback succeeded" in result.stderr
    calls = commands.read_text().splitlines()
    assert "tag classport-frontend:previous classport-frontend:latest" in calls
    restored = [c for c in calls if "up -d --force-recreate classport frontend" in c]
    assert len(restored) == 1 and ("worker" in restored[0]) is previous_worker
