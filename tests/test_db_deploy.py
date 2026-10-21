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
    for name in ("Dockerfile", "docker-compose.yml", ".env", ".env.docker", ".env.migration"):
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
    start = next(i for i, c in enumerate(calls) if c.endswith("up -d classport"))
    assert migrate < seed < start
    assert all("run --rm --no-deps classport" not in c for c in calls)
    if external:
        assert all("up -d postgres" not in c and "classport-postgres" not in c for c in calls)
        assert all("--profile local-db" not in c for c in calls)
    else:
        postgres = next(i for i, c in enumerate(calls) if c.endswith("up -d postgres"))
        assert postgres < migrate
