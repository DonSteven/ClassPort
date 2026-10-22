"""Exercise immutable publishing and remote result propagation without AWS."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from scripts import deploy_via_ssm as ssm
from scripts import publish_release as publisher


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
