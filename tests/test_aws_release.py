"""Exercise the AWS launcher and its versioned application script on a fake host."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
REGISTRY = "123456789012.dkr.ecr.us-east-1.amazonaws.com/classport"
BACKEND = REGISTRY + "/backend@sha256:" + "b" * 64
FRONTEND = REGISTRY + "/frontend@sha256:" + "c" * 64


@pytest.fixture
def aws_host(tmp_path):
    root = tmp_path / "host"
    root.mkdir()
    (root / ".host-ready").touch()
    (root / "data").mkdir()
    (root / "certs").mkdir()
    (root / "certs/global-bundle.pem").touch()
    (root / "aws").mkdir()
    for name in ("bootstrap.py", "docker-compose.prod.yml", "deploy-prod.sh"):
        (root / "aws" / name).write_text("THIS STALE FILE MUST NEVER BE USED")
    host_env = tmp_path / "host.env"
    host_env.write_text(
        f"BACKEND_REPOSITORY={REGISTRY}/backend\nFRONTEND_REPOSITORY={REGISTRY}/frontend\n"
        "CONFIG_SECRET_ARN=application\nDB_SECRET_ARN=database\nPUBLIC_URL=https://study.example.com\n"
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''
import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ['CLASSPORT_DEPLOY_ROOT'])
with open(os.environ['TEST_COMMANDS'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
failure = os.environ.get('TEST_FAILURE', '')
if args[0] == 'pull' and failure == 'pull': sys.exit(1)
if args[:2] == ['image', 'inspect']:
    print('wrong' if failure == 'revision' else os.environ['TEST_COMMIT'])
elif args[0] == 'create': print('release-container')
elif args[0] == 'cp':
    destination = Path(args[-1])
    repo = Path(os.environ['TEST_REPO'])
    for name in ('docker-compose.prod.yml', 'deploy-prod.sh'):
        shutil.copy(repo / name, destination / name)
    shutil.copy(repo / 'infra/aws/scripts/bootstrap.py', destination / 'bootstrap.py')
elif args[0] == 'run':
    target = root / args[-1].removeprefix('/deployment/')
    target.write_text(json.dumps({'status': 'failed' if failure == 'migration' else 'succeeded',
                                 'steps': {'migration': 'failed' if failure == 'migration' else 'succeeded'}}))
    if failure == 'migration': sys.exit(1)
    (root / '.env').touch()
    (root / '.env.smoke').touch()
elif args[0] == 'compose':
    if 'ps' in args: print(args[-1])
    if args[-1] == 'smoke' and failure == 'smoke': sys.exit(1)
elif args[0] == 'inspect':
    print(os.environ['FRONTEND_IMAGE' if args[-1] == 'frontend' else 'BACKEND_IMAGE'])
''')
    docker.chmod(0o755)
    for name, body in {
        "aws": "#!/bin/sh\necho secret-only-in-scratch\n",
        "mountpoint": "#!/bin/sh\nexit 0\n",
        "curl": '#!/bin/sh\necho \'{"ok":true}\'\n',
    }.items():
        binary = binaries / name
        binary.write_text(body)
        binary.chmod(0o755)
    temporary = binaries / "mktemp"
    temporary.write_text(
        f"#!{sys.executable}\nimport subprocess, sys\n"
        f"args = [arg.replace('/run/classport.', {str(tmp_path / 'scratch.')!r}) for arg in sys.argv[1:]]\n"
        f"raise SystemExit(subprocess.call([{shutil.which('mktemp')!r}, *args]))\n"
    )
    temporary.chmod(0o755)
    env = {**os.environ, "CLASSPORT_DEPLOY_ROOT": str(root), "CLASSPORT_HOST_ENV": str(host_env),
           "TEST_COMMANDS": str(tmp_path / "commands.jsonl"), "TEST_COMMIT": COMMIT,
           "TEST_REPO": str(REPO), "PATH": str(binaries) + os.pathsep + os.environ["PATH"]}
    return root, env


@pytest.mark.parametrize("failure", ["", "pull", "revision", "migration", "smoke"])
def test_aws_release_uses_image_files_and_records_every_reachable_failure(aws_host, failure):
    root, env = aws_host
    result = subprocess.run(
        ["bash", str(REPO / "infra/aws/scripts/release.sh"), BACKEND, FRONTEND, COMMIT, "123-1"],
        env={**env, "TEST_FAILURE": failure}, text=True, capture_output=True, timeout=30,
    )
    report = json.loads((root / f"attempts/{COMMIT}.123-1/result.json").read_text())
    calls = [json.loads(line) for line in Path(env["TEST_COMMANDS"]).read_text().splitlines()]
    assert report["manifest"] == {"commit": COMMIT, "backend": BACKEND, "frontend": FRONTEND}
    assert "secret-only-in-scratch" not in json.dumps(report) + result.stdout + result.stderr
    assert not list(root.parent.glob("scratch.*"))
    assert result.returncode == (0 if not failure else 1), result.stdout + result.stderr
    assert report["status"] == ("failed" if failure else "succeeded")
    maintenance = [call for call in calls if call[0] == "run"]
    if failure in ("pull", "revision"):
        assert not maintenance
        assert report["steps"]["database"] == "pending"
    else:
        assert len(maintenance) == 1
        assert "/app/release/bootstrap.py" in maintenance[0]
        assert "/deployment/aws/bootstrap.py" not in maintenance[0]
        assert report["database"]["steps"]["migration"] == ("failed" if failure == "migration" else "succeeded")
    starts = [call for call in calls if call[0] == "compose" and "up" in call]
    if failure in ("pull", "revision", "migration"):
        assert not starts and not (root / "releases/current").exists()
    elif not failure:
        assert report["application"]["current_release"] == report["manifest"]
        assert all(status == "succeeded" for status in report["steps"].values())
    else:
        assert report["application"]["rollback"] == "no_previous_release"
