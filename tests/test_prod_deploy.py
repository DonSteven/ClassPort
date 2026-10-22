"""Exercise production release state and failure recovery without a real host."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]


def _bundle(path, number):
    path.mkdir(parents=True)
    (path / "release.env").write_text(
        f"RELEASE_COMMIT={str(number) * 40}\n"
        f"BACKEND_IMAGE=ghcr.io/example/backend@sha256:{str(number) * 64}\n"
        f"FRONTEND_IMAGE=ghcr.io/example/frontend@sha256:{str(number) * 64}\n"
    )
    for name in ("docker-compose.prod.yml", "deploy-prod.sh"):
        shutil.copy(REPO / name, path / name)
    return path


@pytest.fixture
def host(tmp_path):
    root = tmp_path / "host"
    root.mkdir()
    for name in (".env", ".env.smoke"):
        (root / name).touch()
    (root / "certs").mkdir()
    (root / "certs/global-bundle.pem").touch()
    (root / "data").mkdir()
    old = _bundle(root / "releases/old", 1)
    previous = _bundle(root / "releases/older", 2)
    (root / "releases/current").symlink_to(old)
    (root / "releases/previous").symlink_to(previous)
    candidate = _bundle(tmp_path / "candidate", 3)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "image = os.environ.get('BACKEND_IMAGE', '')\n"
        "with open(os.environ['TEST_COMMANDS'], 'a') as log:\n"
        "    log.write(json.dumps({'args': args, 'backend': image, "
        "'frontend': os.environ.get('FRONTEND_IMAGE')}) + '\\n')\n"
        "if 'ps' in args: print(args[-1]); sys.exit(0)\n"
        "if args[0] == 'inspect':\n"
        "    if image.endswith('3' * 64) and os.environ.get('TEST_FAIL_STAGE') == 'images': print('wrong-image'); sys.exit(0)\n"
        "    print(os.environ['FRONTEND_IMAGE' if args[-1] == 'frontend' else 'BACKEND_IMAGE']); sys.exit(0)\n"
        "stage = ''\n"
        "if 'pull' in args and 'run' not in args and 'up' not in args: stage = 'pull'\n"
        "elif 'up' in args: stage = 'up'\n"
        "elif 'smoke' in args: stage = 'smoke'\n"
        "if image.endswith('3' * 64) and stage == os.environ.get('TEST_FAIL_STAGE', '!'):\n"
        "    sys.exit(1)\n"
        "if image.endswith('1' * 64) and stage == 'up' and os.environ.get('TEST_FAIL_OLD'):\n"
        "    sys.exit(1)\n"
    )
    docker.chmod(0o755)
    curl = binaries / "curl"
    curl.write_text(
        '#!/bin/sh\n'
        'case "$BACKEND_IMAGE:$TEST_FAIL_STAGE" in\n'
        '  *3333333333333333333333333333333333333333333333333333333333333333:ready) exit 1 ;;\n'
        'esac\n'
        'echo \'{"ok":true}\'\n'
    )
    curl.chmod(0o755)
    mountpoint = binaries / "mountpoint"
    mountpoint.write_text('#!/bin/sh\n[ "${TEST_DISK_MISSING:-0}" = 0 ]\n')
    mountpoint.chmod(0o755)
    env = {
        **os.environ, "CLASSPORT_DEPLOY_ROOT": str(root), "CLASSPORT_HEALTH_TIMEOUT": "1",
        "PATH": f"{binaries}:{os.environ['PATH']}", "TEST_COMMANDS": str(tmp_path / "calls"),
        "TEST_FAIL_STAGE": "none",
    }
    return root, candidate, env


def _run(host, *args, **settings):
    root, candidate, env = host
    result = subprocess.run(
        ["bash", str(REPO / "deploy-prod.sh"), *(args or [str(candidate)])],
        env={**env, **settings}, capture_output=True, text=True, timeout=15,
    )
    calls = Path(env["TEST_COMMANDS"])
    return result, [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []


def test_production_deploy_records_a_verified_pair_after_database_preparation(host):
    root, _, _ = host
    result, calls = _run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    current = (root / "releases/current").resolve()
    assert "RELEASE_COMMIT=" + "3" * 40 in (current / "release.env").read_text()
    assert (root / "releases/previous").resolve() == root / "releases/old"
    args = [call["args"] for call in calls]
    pull = next(i for i, a in enumerate(args) if "pull" in a and "run" not in a and "up" not in a)
    up = next(i for i, a in enumerate(args) if "up" in a)
    smoke = next(i for i, a in enumerate(args) if a[-1] == "smoke")
    assert pull < up < smoke
    assert not any("migrate" in a or "scripts/seed_operator_password.py" in a for a in args)
    assert "--no-build" in args[up]
    assert args[up][-2:] == ["classport", "frontend"]
    assert all("build" not in a and "postgres" not in a for a in args)
    assert all("--project-directory" in a and "-f" in a for a in args if a[0] == "compose")
    report = json.loads((current / "application-result.json").read_text())
    assert report["status"] == "succeeded"
    assert all(value == "succeeded" for value in report["steps"].values())
    assert report["current_release"]["backend"].endswith("3" * 64)


@pytest.mark.parametrize("stage", ["pull", "up", "ready", "smoke", "images"])
def test_failed_release_preserves_success_records_and_restores_pair_after_switch(host, stage):
    root, _, _ = host
    result, calls = _run(host, TEST_FAIL_STAGE=stage)
    assert result.returncode == 1, result.stdout + result.stderr
    assert (root / "releases/current").resolve() == root / "releases/old"
    assert (root / "releases/previous").resolve() == root / "releases/older"
    report = json.loads(next((root / "releases").glob("3*/application-result.json")).read_text())
    assert report["status"] == "failed"
    assert report["current_release"]["backend"].endswith("1" * 64)
    restores = [c for c in calls if "up" in c["args"] and c["backend"].endswith("1" * 64)]
    assert bool(restores) == (stage in ("up", "ready", "smoke", "images"))
    if restores:
        assert restores[0]["frontend"].endswith("1" * 64)
        assert str(root / "releases/old/docker-compose.prod.yml") in restores[0]["args"]
        assert any(c["args"][-1] == "smoke" and c["backend"].endswith("1" * 64) for c in calls)


def test_manual_rollback_uses_previous_pair_without_migrations(host):
    root, _, _ = host
    result, calls = _run(host, "--rollback")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "releases/current").resolve() == root / "releases/older"
    assert (root / "releases/previous").resolve() == root / "releases/old"
    assert not any("migrate" in call["args"] for call in calls)
    assert any("up" in c["args"] and c["frontend"].endswith("2" * 64) for c in calls)


def test_failed_first_release_does_not_become_current(host):
    root, _, _ = host
    (root / "releases/current").unlink()
    (root / "releases/previous").unlink()
    result, _ = _run(host, TEST_FAIL_STAGE="smoke")
    assert result.returncode == 1
    assert not (root / "releases/current").exists()


def test_failed_rollback_reports_unrecovered_service(host):
    result, _ = _run(host, TEST_FAIL_STAGE="smoke", TEST_FAIL_OLD="1")
    assert result.returncode == 2


def test_mutable_image_reference_is_rejected_before_docker(host):
    _, candidate, _ = host
    manifest = candidate / "release.env"
    manifest.write_text(manifest.read_text().replace("@sha256:" + "3" * 64, ":latest"))
    result, calls = _run(host)
    assert result.returncode == 1
    assert calls == []


def test_missing_data_mount_stops_before_docker(host):
    result, calls = _run(host, TEST_DISK_MISSING="1")
    assert result.returncode == 1
    assert calls == []


def test_concurrent_deploy_is_rejected(host):
    fcntl = pytest.importorskip("fcntl")
    root, _, _ = host
    with (root / "releases/deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, calls = _run(host)
    assert result.returncode == 1
    assert calls == []


def test_repeated_release_preserves_previous_pair_for_manual_rollback(host):
    root, _, _ = host
    first, _ = _run(host)
    assert first.returncode == 0, first.stdout + first.stderr
    previous = (root / "releases/previous").resolve()

    repeated, _ = _run(host)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert (root / "releases/previous").resolve() == previous

    rollback, calls = _run(host, "--rollback")
    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    assert (root / "releases/current").resolve() == previous
    assert [c for c in calls if "up" in c["args"]][-1]["backend"].endswith("1" * 64)


def test_same_images_preserve_previous_despite_manifest_format_or_commit_change(host):
    root, candidate, _ = host
    first, _ = _run(host)
    assert first.returncode == 0, first.stdout + first.stderr
    previous = (root / "releases/previous").resolve()
    manifest = candidate / "release.env"
    lines = manifest.read_text().replace("RELEASE_COMMIT=" + "3" * 40, "RELEASE_COMMIT=" + "4" * 40).splitlines()
    manifest.write_text("# Same image pair\n" + "\n".join(reversed(lines)) + "\n")

    repeated, _ = _run(host)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert (root / "releases/previous").resolve() == previous


def test_same_images_with_changed_compose_keep_previous_configuration(host):
    root, candidate, _ = host
    first, _ = _run(host)
    assert first.returncode == 0, first.stdout + first.stderr
    original = (root / "releases/current").resolve()
    config = candidate / "docker-compose.prod.yml"
    config.write_text(config.read_text().replace("restart: unless-stopped", "restart: always"))

    updated, _ = _run(host)
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert (root / "releases/previous").resolve() == original
    rollback, _ = _run(host, "--rollback")
    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    assert (root / "releases/current").resolve() == original


def test_repeated_first_release_does_not_create_a_self_rollback_target(host):
    root, _, _ = host
    (root / "releases/current").unlink()
    (root / "releases/previous").unlink()
    for _ in range(2):
        result, _ = _run(host)
        assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "releases/previous").exists()


def _interrupt_promotion(host, *, before_move=False):
    root, _, env = host
    binaries = Path(env["PATH"].split(os.pathsep)[0])
    move = binaries / "mv"
    real_move = shutil.which("mv")
    move.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, subprocess, sys\n"
        "from pathlib import Path\n"
        f"target = Path({str(root / 'releases/current')!r})\n"
        f"marker = Path({str(binaries / 'promotion-interrupted')!r})\n"
        "interrupt = Path(sys.argv[-1]) == target and not marker.exists()\n"
        f"if interrupt and {before_move!r}:\n"
        "    marker.touch()\n"
        "    sys.exit(1)\n"
        f"result = subprocess.run([{real_move!r}, *sys.argv[1:]])\n"
        "if result.returncode == 0 and interrupt:\n"
        "    marker.touch()\n"
        "    os.kill(os.getppid(), signal.SIGTERM)\n"
        "sys.exit(result.returncode)\n"
    )
    move.chmod(0o755)


@pytest.mark.parametrize("before_move", [True, False], ids=["failed-current-update", "terminated-after-current-update"])
@pytest.mark.parametrize("rollback", [False, True], ids=["deploy", "rollback"])
def test_interrupted_promotion_restores_both_records_and_running_pair(host, before_move, rollback):
    root, _, _ = host
    original = (root / "releases/current").resolve()
    previous = (root / "releases/previous").resolve()
    _interrupt_promotion(host, before_move=before_move)

    result, calls = _run(host, *(["--rollback"] if rollback else []))
    assert result.returncode == 1, result.stdout + result.stderr
    assert (root / "releases/current").resolve() == original
    assert (root / "releases/previous").resolve() == previous
    restored = [c for c in calls if "up" in c["args"]][-1]
    assert restored["backend"].endswith("1" * 64)
    assert restored["frontend"].endswith("1" * 64)


def test_interrupted_first_promotion_leaves_no_success_records(host):
    root, _, _ = host
    (root / "releases/current").unlink()
    (root / "releases/previous").unlink()
    _interrupt_promotion(host)

    result, _ = _run(host)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not (root / "releases/current").exists()
    assert not (root / "releases/previous").exists()
