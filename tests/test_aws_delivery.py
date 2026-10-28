"""Exercise immutable publishing and remote result propagation without AWS."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from scripts import deploy_via_ssm as ssm
from scripts import publish_release as publisher
from scripts import release_local as local


COMMIT = "a" * 40
BACKEND_REPO = "123456789012.dkr.ecr.us-east-1.amazonaws.com/classport/backend"
FRONTEND_REPO = "123456789012.dkr.ecr.us-east-1.amazonaws.com/classport/frontend"
BACKEND = BACKEND_REPO + "@sha256:" + "b" * 64
FRONTEND = FRONTEND_REPO + "@sha256:" + "c" * 64
MANIFEST = {"RELEASE_COMMIT": COMMIT, "BACKEND_IMAGE": BACKEND, "FRONTEND_IMAGE": FRONTEND}
PAIR = {"commit": COMMIT, "backend": BACKEND, "frontend": FRONTEND}


def test_partial_publish_reuses_backend_and_builds_only_missing_frontend(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://study.example.com")
    monkeypatch.setattr(publisher, "find_digest", Mock(side_effect=["sha256:" + "b" * 64, None, "sha256:" + "c" * 64]))
    build = Mock()
    monkeypatch.setattr(publisher.subprocess, "run", build)
    images = publisher.publish(COMMIT, BACKEND_REPO, FRONTEND_REPO, tmp_path)
    assert images["backend"]["reused"] is True
    assert images["frontend"]["reused"] is False
    build.assert_called_once()
    args = build.call_args.args[0]
    assert "web/Dockerfile" in args and f"{FRONTEND_REPO}:{COMMIT}" in args
    assert ssm.read_manifest(tmp_path / "release.env") == MANIFEST


def test_rerun_reuses_both_images(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://study.example.com")
    monkeypatch.setattr(publisher, "find_digest", Mock(side_effect=["sha256:" + "b" * 64, "sha256:" + "c" * 64]))
    build = Mock()
    monkeypatch.setattr(publisher.subprocess, "run", build)
    assert all(image["reused"] for image in publisher.publish(COMMIT, BACKEND_REPO, FRONTEND_REPO, tmp_path).values())
    build.assert_not_called()


def test_ecr_authorization_error_does_not_trigger_build(monkeypatch):
    monkeypatch.setattr(publisher.subprocess, "run", Mock(return_value=subprocess.CompletedProcess(
        [], 254, "", "An error occurred (AccessDeniedException)")))
    with pytest.raises(RuntimeError, match="refusing"):
        publisher.find_digest(BACKEND_REPO, COMMIT)


def host_result():
    return {"manifest": PAIR, "run": "123-1", "status": "succeeded",
            "database": {"status": "succeeded", "steps": {"migration": "succeeded"}},
            "application": {"status": "succeeded", "current_release": PAIR.copy()}}


def invocation(host, status="Success", code=0):
    return {"Status": status, "ResponseCode": code,
            "StandardOutputContent": "release log\n" + ssm.MARKER + json.dumps(host) + "\n",
            "StandardErrorContent": ""}


def test_ssm_waits_for_completion_and_records_the_exact_running_pair(monkeypatch, tmp_path):
    request = Mock(side_effect=[{"Command": {"CommandId": "command-123"}}, LookupError(),
                                {"Status": "InProgress"}, invocation(host_result())])
    monkeypatch.setattr(ssm, "aws_json", request)
    monkeypatch.setattr(ssm.time, "sleep", lambda _: None)
    assert ssm.deploy(MANIFEST, "i-" + "1" * 17, "123-1", tmp_path) == 0
    report = json.loads((tmp_path / "ssm-result.json").read_text())
    assert report["command_id"] == "command-123" and report["status"] == "succeeded"
    assert report["release"]["application"]["current_release"] == PAIR
    assert request.call_count == 4
    command = json.loads(request.call_args_list[0].args[-1])["commands"][0]
    assert "/usr/local/sbin/classport-release" in command
    assert COMMIT in command and BACKEND in command and FRONTEND in command


@pytest.mark.parametrize("failure", ["remote_failure", "wrong_running_image", "missing_record", "migration_failure"])
def test_ssm_rejects_incomplete_or_failed_release(monkeypatch, tmp_path, failure):
    host = host_result()
    status, code = "Success", 0
    if failure == "remote_failure":
        status, code = "Failed", 1
        host["status"] = "failed"
        host["application"] = {"rollback": "succeeded", "current_release": {"commit": "d" * 40}}
    elif failure == "wrong_running_image":
        host["application"]["current_release"]["frontend"] = "wrong"
    elif failure == "missing_record":
        host = None
    else:
        host["database"]["steps"]["migration"] = "failed"
    monkeypatch.setattr(ssm, "aws_json", Mock(side_effect=[{"Command": {"CommandId": "command-123"}},
                                                           invocation(host, status, code)]))
    assert ssm.deploy(MANIFEST, "i-" + "1" * 17, "123-1", tmp_path) == 1
    assert json.loads((tmp_path / "ssm-result.json").read_text())["status"] == "failed"


def test_ssm_timeout_retains_command_id_and_does_not_resubmit(monkeypatch, tmp_path):
    request = Mock(return_value={"Command": {"CommandId": "still-running"}})
    monkeypatch.setattr(ssm, "aws_json", request)
    assert ssm.deploy(MANIFEST, "i-" + "1" * 17, "123-1", tmp_path, timeout=0) == 1
    request.assert_called_once()
    report = json.loads((tmp_path / "ssm-result.json").read_text())
    assert report["status"] == "timed_out" and report["command_id"] == "still-running"


def test_ssm_query_failure_retains_unconfirmed_remote_state(monkeypatch, tmp_path):
    monkeypatch.setattr(ssm, "aws_json", Mock(side_effect=[{"Command": {"CommandId": "sent"}},
                                                           RuntimeError("query failed")]))
    assert ssm.deploy(MANIFEST, "i-" + "1" * 17, "123-1", tmp_path) == 1
    report = json.loads((tmp_path / "ssm-result.json").read_text())
    assert report["status"] == "unconfirmed" and report["command_id"] == "sent"


def test_local_release_refuses_uncommitted_source_before_aws_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(local, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    commands = Mock(return_value=" M app/main.py\n")
    monkeypatch.setattr(local.subprocess, "check_output", commands)
    publish = Mock()
    monkeypatch.setattr(publisher, "publish", publish)
    with pytest.raises(RuntimeError, match="Commit all source"):
        local.release("operator")
    commands.assert_called_once_with(["git", "status", "--porcelain"], text=True)
    publish.assert_not_called()


@pytest.mark.parametrize("check_fails", [False, True])
def test_local_release_deploys_only_after_exact_image_checks(monkeypatch, tmp_path, check_fails):
    monkeypatch.setattr(local, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(local.os, "environ", local.os.environ.copy())
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-credential-must-not-win")
    outputs = {key: {"value": value} for key, value in {
        "aws_region": "us-east-1", "instance_id": "i-" + "1" * 17,
        "public_url": "https://study.example.com", "backend_repository": BACKEND_REPO,
        "frontend_repository": FRONTEND_REPO,
    }.items()}
    monkeypatch.setattr(local.subprocess, "check_output", Mock(side_effect=[
        "", COMMIT, json.dumps(outputs),
        json.dumps({"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:role/operator"}),
        b"temporary-registry-password",
    ]))

    def publish(commit, backend, frontend, output):
        (output / "release.env").write_text("\n".join(f"{k}={v}" for k, v in MANIFEST.items()))

    monkeypatch.setattr(publisher, "publish", publish)

    def run(args, **kwargs):
        if args[0] == "uv":
            assert local.os.environ["CLASSPORT_TEST_BACKEND_IMAGE"] == BACKEND
            assert local.os.environ["CLASSPORT_TEST_FRONTEND_IMAGE"] == FRONTEND
            assert local.os.environ["AWS_PROFILE"] == "operator"
            assert "AWS_ACCESS_KEY_ID" not in local.os.environ
            if check_fails:
                raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(local.subprocess, "run", Mock(side_effect=run))
    deploy = Mock(return_value=0)
    monkeypatch.setattr(ssm, "deploy", deploy)
    if check_fails:
        with pytest.raises(subprocess.CalledProcessError):
            local.release("operator")
        deploy.assert_not_called()
    else:
        assert local.release("operator") == 0
        assert deploy.call_args.args[:2] == (MANIFEST, "i-" + "1" * 17)
        assert deploy.call_count == 1
